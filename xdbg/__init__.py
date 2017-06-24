from .xdbg import Debugger

error = None
try:
    get_ipython().kernel
except NameError:
    error = "xdbg must be run inside an IPython kernel"
except AttributeError:
    error = "xdbg must be run inside an IPython kernel. A running IPython instance was found, but it does not appear to have an associated kernel"

if error is not None:
    raise ImportError(error)

debugger = Debugger(get_ipython())

__all__ = ['debugger']
