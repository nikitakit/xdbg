import inspect
import sys, os
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer import StatelessInputTransformer
from IPython.core.splitinput import LineInfo
from IPython.core.magic import (Magics, magics_class, line_magic,
                                cell_magic, line_cell_magic)
from .frame_tracker import FrameTracker
import ast
import types
import importlib

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
                return self.debugger.frame_tracker.main_module
        return self.filename_to_module[filename]

    @line_magic
    def scope(self, line):
        module = None
        if not line or line == '__main__':
            module = self.debugger.frame_tracker.main_module
        elif line.endswith('.py'):
            module = self.lookup_module(line)
        elif line in sys.modules:
            module = sys.modules[line]

        if module is not None:
            self.debugger.frame_tracker.enter_module(module)
        else:
            print("Scope not found", file=sys.stderr)

class Debugger():
    instance = None

    def __init__(self):
        Debugger.instance = self

        self.shell = get_ipython()
        self.frame_tracker = FrameTracker.get_instance()

        # Initialize the quasimagics dictionary
        self.quasimagics = {}
        self.quasimagics['break'] = '_break_handler'

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
        if Debugger.instance is None:
            Debugger.instance = Debugger()

        return Debugger.instance

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
            return "return get_ipython()._xdbg_frame_tracker.enter_frame(__name__, locals())"
        else:
            return "{val} = get_ipython()._xdbg_frame_tracker.replace_with_proxy({val})".format(val=lineinf.the_rest)

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
            return self.frame_tracker.enter_frame(
                    func.__module__,
                    locals_dict,
                    frame_name=func.__name__,
                    closure_dict=closure_dict)
        proxy.__dict__ = func.__dict__
        proxy.__name__ = func.__name__
        proxy.__qualname__ = func.__qualname__
        proxy._func = func
        return proxy
