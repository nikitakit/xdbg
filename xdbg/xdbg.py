import inspect
import sys, os
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer import StatelessInputTransformer
from IPython.core.splitinput import LineInfo
from IPython.core.magic import (Magics, magics_class, line_magic,
                                cell_magic, line_cell_magic)
from ipykernel.comm.comm import Comm
from .exec_scope import ExecScope
import ast
import types
import importlib

__all__ = ["Debugger"]

class ReturnRewriter(ast.NodeTransformer):
    def __init__(self, debugger):
        self.debugger = debugger

    def visit_FunctionDef(self, node):
        return node

    def visit_AsyncFunctionDef(self, node):
        return node

    def visit_ClassDef(self, node):
        return node

    def visit_Return(self, node):
        expr_args = self.debugger.get_return_call_ast()
        if expr_args is None:
            return node
        else:
            expr, args = expr_args
            if node.value is not None:
                args.append(node.value)
            return expr

@magics_class
class DebuggerMagics(Magics):
    def __init__(self, shell, debugger):
        super().__init__(shell)
        self.debugger = debugger
        self.update_modules()

    def update_modules(self):
        self.filename_to_module = {}
        for module in sys.modules.values():
            try:
                filename = inspect.getfile(module)
            except TypeError:
                continue # Module has no file
            self.filename_to_module[filename] = module

    def lookup_module(self, filename):
        filename = os.path.expanduser(os.path.expandvars(filename))
        for prefix, alternative in self.debugger.path_mapping.items():
            prefix = os.path.expanduser(os.path.expandvars(prefix))
            alternative = os.path.expanduser(os.path.expandvars(alternative))
            try:
                if os.path.commonpath([filename, prefix]) == os.path.commonpath([prefix]):
                    rel_filename = os.path.relpath(filename, prefix)
                    filename = os.path.join(alternative, rel_filename)
            except ValueError:
                pass

        if filename not in self.filename_to_module:
            self.update_modules()
            if filename not in self.filename_to_module:
                return self.debugger.main_module
        return self.filename_to_module[filename]

    @line_magic
    def scope(self, line):
        self.debugger.enter_module(self.lookup_module(line))

class Debugger():
    def __init__(self):
        self.shell = get_ipython()
        self.main_module = self.shell.user_module
        if hasattr(self.shell, '_debugger'):
            raise ValueError("Can't create a second Debugger, use Debugger.get_instance() instead")
        self.shell._debugger = self

        # Set up a comm for our current frontend
        # If the frontend is reloaded in the future, register a comm target so
        # the frontend can replace our comm instance
        # TODO: support multiple simultaneous comms
        self.comm = Comm(target_name="xdbg")
        self.shell.kernel.comm_manager.register_target("xdbg", self.change_comm)

        self.frames = []
        self.frames.append({
            'temporary': True,
            'module': self.main_module,
            'frame_name': '__main__'
        })

        # Initialize the quasimagics dictionary
        self.quasimagics = {}
        self.quasimagics['break'] = '_break_handler'

        # Initialize the return handler
        self.shell.ast_transformers.append(ReturnRewriter(self))

        # Initialize the quasi-magic handler
        for transforms_list in [
            self.shell.input_transformer_manager.logical_line_transforms,
            self.shell.input_splitter.logical_line_transforms,
                ]:
            to_remove = []
            for x in transforms_list:
                try:
                    if x.func.__name__  == 'create_quasimagic_handler':
                        to_remove.append(x)
                except:
                    # Most likely the transform was not created using the provided
                    # decorators
                    pass
            for x in to_remove:
                transforms_list.remove(x)
        self.quasimagic_handler = StatelessInputTransformer.wrap(self.create_quasimagic_handler)()
        self.shell.input_transformer_manager.logical_line_transforms.insert(0, self.quasimagic_handler)
        self.shell.input_splitter.logical_line_transforms.insert(0, self.quasimagic_handler)

        # Initialize real magics
        self.path_mapping = {}
        self.magics = DebuggerMagics(self.shell, self)
        self.shell.register_magics(self.magics)

    @staticmethod
    def get_instance():
        shell = get_ipython()
        if not hasattr(shell, '_debugger'):
            shell._debugger = Debugger()

        return shell._debugger

    def change_comm(self, comm, msg):
        if self.comm is not None and self.comm is not comm:
            self.comm.close()
        self.comm = comm

    def import_module(self, name):
        """
        Import a module without running the code inside
        """
        if name in sys.modules:
            return
        spec = importlib.util.find_spec(name)
        sys.modules[name] = importlib.util.module_from_spec(spec)

    def create_quasimagic_handler(self, line):
        """
        Handler for the quasi-magic %break.

        It needs access to locals() from the correct frame, so for now it's
        implemented as its own input transformer.
        """
        lineinf = LineInfo(line)
        if lineinf.esc == '%' and lineinf.ifun in self.quasimagics:
            return lineinf.pre + getattr(self, self.quasimagics[lineinf.ifun])(lineinf)
        return line

    def _break_handler(self, lineinf):
        if not lineinf.the_rest:
            return "return get_ipython()._debugger.enter_frame(__name__, locals())"
        else:
            return "{val} = get_ipython()._debugger.replace_with_proxy({val})".format(val=lineinf.the_rest)

    def get_return_call_ast(self):
        if not self.frames or self.frames[-1]['temporary']:
            return None
        module = ast.parse('get_ipython()._debugger.exit_frame(1)')
        expr = module.body[0]
        args = module.body[0].value.args
        args.pop()
        return expr, args

    def enter_module(self, module):
        if not self.frames or not self.frames[-1]['temporary']:
            return

        if self.frames[-1]['module'] == module:
            return

        frame = self.frames[-1]
        frame['module'] = module
        frame['frame_name'] = module.__name__

        if '_oh' not in module.__dict__:
            module._oh = {}
        self.shell.user_module = module
        self.shell.user_ns = module.__dict__
        self.comm.send(data={'scope': module.__name__})

    def enter_frame(self, module_name, locals_dict, frame_name=None, closure_dict=None):
        if '_oh' not in locals_dict:
            locals_dict['_oh'] = {}

        try:
            module = sys.modules[module_name]
        except KeyError:
            module = self.main_module

        if 'get_ipython' not in module.__dict__:
            module.get_ipython = get_ipython

        frame = {
            'frame_name': frame_name if frame_name is not None else "<unknown>",
            'old_module': self.shell.user_module,
            'old_locals': self.shell.user_ns,
            'module': module,
            'locals': locals_dict,
            'has_returned': False,
            'return_value': None,
            'exec_scope': ExecScope(module.__dict__,
                locals_dict,
                shell=self.shell,
                closure_dict=closure_dict),
            'old_run_ast_nodes': self.shell.run_ast_nodes,
            'temporary': False,
        }

        if frame_name is None:
            try:
                stack = inspect.stack()
                frame_name = '<{}>.{}'.format(module_name, stack[1].function)
                frame['frame_name'] = frame_name
            except:
                pass
        self.comm.send(data={'scope': frame_name})

        self.frames.append(frame)

        self.shell.user_module = frame['module']
        self.shell.user_ns = frame['locals']

        print('[xdbg] Entered:', frame['frame_name'])
        if closure_dict is None and inspect.currentframe().f_back.f_code.co_freevars:
            # There's no reliable way to get closure info at runtime... but,
            # the %break obj syntax that creates proxy objects can get closure
            # info
            print('[xdgb] Warning: nonlocals copied by value')
        self.shell.execution_count += 1 # Needed to keep ID's unique
        self.shell.run_ast_nodes = frame['exec_scope'].shell_substitute_run_ast_nodes

        # Need to continue the main kernel loop without returning from here
        try:
            while not frame['has_returned']:
                self.shell.get_ipython().kernel.do_one_iteration()
            return frame['return_value']
        except:
            raise
        finally:
            print('[xdbg] Exited:', frame['frame_name'])
            self.shell.run_ast_nodes = frame['old_run_ast_nodes']
            self.shell.user_module = frame['old_module']
            self.shell.user_ns = frame['old_locals']
            if self.frames:
                self.comm.send(data={'scope': self.frames[-1]['frame_name']})


    def exit_frame(self, val=None):
        frame = self.frames.pop()
        frame['return_value'] = val
        frame['has_returned'] = True

    def replace_with_proxy(self, func):
        if not isinstance(func, (types.FunctionType, types.MethodType)):
            raise ValueError("Can only break on functions or methods")
        sig = inspect.signature(func)
        closure_dict = {}
        if func.__code__.co_freevars:
            closure_dict = dict(zip(func.__code__.co_freevars, func.__closure__))
        def proxy(*args, **kwargs):
            bound_arguments = sig.bind(*args, **kwargs)
            bound_arguments.apply_defaults()
            locals_dict = dict(bound_arguments.arguments)
            if isinstance(func, types.MethodType):
                try:
                    # First parameter is typically named 'self', but python does not
                    # inforce this
                    self_name = list(inspect.signature(func.__func__).parameters.keys())[0]
                    locals_dict[self_name] = func.__self__
                except:
                    # TODO: fix behavior for methods that incorrectly fail to take
                    # self as an argument
                    pass
            return self.enter_frame(func.__globals__,
                    locals_dict,
                    frame_name=func.__name__,
                    closure_dict=closure_dict)
        proxy.__dict__ = func.__dict__
        proxy.__name__ = func.__name__
        proxy.__qualname__ = func.__qualname__
        proxy._func = func
        return proxy
