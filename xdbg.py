import inspect
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.inputtransformer import StatelessInputTransformer
from IPython.core.splitinput import LineInfo
import types

__all__ = ["Debugger"]

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
        for transforms_list in [
            self.shell.input_transformer_manager.python_line_transforms,
            self.shell.input_splitter.python_line_transforms,
                ]:
            to_remove = []
            for x in transforms_list:
                try:
                    if x.func.__name__  == 'create_return_handler':
                        to_remove.append(x)
                except:
                    # Most likely the transform was not created using the provided
                    # decorators
                    pass
            for x in to_remove:
                transforms_list.remove(x)

        self.return_handler = StatelessInputTransformer.wrap(self.create_return_handler)()
        self.shell.input_splitter.python_line_transforms.append(self.return_handler)
        self.shell.input_transformer_manager.python_line_transforms.append(self.return_handler)

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

    def create_return_handler(self, line):
        lineinf = LineInfo(line)
        if lineinf.esc == "" and lineinf.ifun == "return":
            return_function = self.get_return_function()
            if return_function:
                return lineinf.pre + return_function + '(' + lineinf.the_rest + ')'
        return line

    def _break_handler(self, lineinf):
        if not lineinf.the_rest:
            return "return get_ipython()._debugger.enter_frame(globals(), locals())"
        else:
            return "{val} = get_ipython()._debugger.replace_with_proxy({val})".format(val=lineinf.the_rest)

    def get_return_function(self):
        if self.frames:
            return 'get_ipython()._debugger.exit_frame'
        else:
            return None

    def enter_frame(self, globals_dict, locals_dict, frame_name=None):
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
        self.shell.execution_count += 1 # Needed to keep ID's unique

        # Need to continue the main kernel loop without returning from here
        try:
            while not frame['has_returned']:
                  self.shell.get_ipython().kernel.do_one_iteration()
            return frame['return_value']
        except:
            raise
        finally:
            print('[DBG] Exited:', frame['frame_name'])
            self.shell.user_ns = frame['old_locals']

    def exit_frame(self, val=None, *args):
        frame = self.frames.pop()
        if not args:
            frame['return_value'] = val
        else:
            frame['return_value'] = (val,) + args
        frame['has_returned'] = True

    def replace_with_proxy(self, func):
        if not isinstance(func, (types.FunctionType, types.MethodType)):
            raise ValueError("Can only break on functions or methods")
        sig = inspect.signature(func)
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
            return self.enter_frame(func.__globals__, locals_dict, frame_name=func.__name__)
        proxy.__dict__ = func.__dict__
        proxy.__name__ = func.__name__
        proxy.__qualname__ = func.__qualname__
        proxy._func = func
        return proxy
