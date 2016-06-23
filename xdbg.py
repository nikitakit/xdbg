import inspect
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer import StatelessInputTransformer
from IPython.core.splitinput import LineInfo
from exec_scope import ExecScope
import ast
import types

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

class Debugger():
    def __init__(self):
        self.shell = get_ipython()
        if hasattr(self.shell, '_debugger'):
            raise ValueError("Can't create a second Debugger, use Debugger.get_instance() instead")
        self.shell._debugger = self

        self.frames = []

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

    @staticmethod
    def get_instance():
        shell = get_ipython()
        if not hasattr(shell, '_debugger'):
            shell._debugger = Debugger()

        return shell._debugger

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
            return "return get_ipython()._debugger.enter_frame(globals(), locals())"
        else:
            return "{val} = get_ipython()._debugger.replace_with_proxy({val})".format(val=lineinf.the_rest)

    def get_return_call_ast(self):
        if not self.frames:
            return None
        module = ast.parse('get_ipython()._debugger.exit_frame(1)')
        expr = module.body[0]
        args = module.body[0].value.args
        args.pop()
        return expr, args

    def enter_frame(self, globals_dict, locals_dict, frame_name=None, closure_dict=None):
        if not globals_dict == self.shell.user_global_ns:
            print("Failed to enter frame with wrong globals")
            return

        if '_oh' not in locals_dict:
            locals_dict['_oh'] = {}

        if 'get_ipython' not in globals_dict:
            locals_dict['get_ipython'] = get_ipython

        frame = {
            'frame_name': frame_name if frame_name is not None else "<unknown>",
            'old_locals': self.shell.user_ns,
            'globals': globals_dict,
            'locals': locals_dict,
            'has_returned': False,
            'return_value': None,
            'exec_scope': ExecScope(globals_dict,
                locals_dict,
                shell=self.shell,
                closure_dict=closure_dict),
            'old_run_ast_nodes': self.shell.run_ast_nodes,
        }

        if frame_name is None:
            try:
                stack = inspect.stack()
                frame_name = stack[1].function
                frame['frame_name'] = frame_name
            except:
                pass

        self.frames.append(frame)

        self.shell.user_ns = frame['locals']

        print('[DBG] Entered:', frame['frame_name'])
        if closure_dict is None and inspect.currentframe().f_back.f_code.co_freevars:
            # There's no reliable way to get closure info at runtime... but,
            # the %break obj syntax that creates proxy objects can get closure
            # info
            print('[DBG] Warning: nonlocals copied by value')
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
            print('[DBG] Exited:', frame['frame_name'])
            self.shell.run_ast_nodes = frame['old_run_ast_nodes']
            self.shell.user_ns = frame['old_locals']

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
