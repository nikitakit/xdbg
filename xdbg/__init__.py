from .xdbg import Debugger
import sys

def load_ipython_extension(ipython):
    ipython.xdbg = Debugger(ipython)

def unload_ipython_extension(ipython):
    print("WARNING: unloading xdbg is not implemented", file=sys.stderr)

__all__ = ['load_ipython_extension', 'unload_ipython_extension']
