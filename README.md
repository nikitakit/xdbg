# xdbg
See http://kitaev.io/xdbg for more details.

`xdbg` provides advanced live coding for Python.

It is best used in combination with the [deuterium package](https://github.com/nikitakit/deuterium) for the Atom text editor.

## Features
`xdbg` extends IPython to allow executing live code at any scope within the running program, including:
  * Within any imported module
  * Inside function scope (including support for closures)

## Installation

### Dependencies
* Python 3.5+
* IPython

### To Install

`xdbg` is a valid package because it has an `__init__.py`. At the moment it must be manually imported. Packages for `xdbg` are a work in progress.

## Limitations
New features coming soon...
* Better support for live-editing classes
* Allow live-editing modules while execution is paused inside a function

Known issues:
* `%break ARG` is temporarily broken
* `%break` with no arguments is not useful at module scope

Wishlist
* Support for disabling breakpoints, listing breakpoints, as well as conditional and temporary breakpoints
* Switch to using true breakpoints instead of overriding functions with a proxy object. (Very tricky, or maybe impossible, to implement. `xdbg` allows `return`ing from the breakpoint location with a user-specified value, which is unsupported by `pdb` or any other Python debugger I know of.)

## Documentation

For more details, see http://kitaev.io/xdbg
