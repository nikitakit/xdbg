import ast
import types
import sys

class ExecScope():
    def __init__(self, globals_dict, locals_dict, shell=None, closure_dict=None):
        self.globals_dict = globals_dict
        self.locals_cells = {k:self.create_cell(v) for k,v in locals_dict.items()}
        if closure_dict is not None:
            self.locals_cells.update(closure_dict)
        self.shell = shell

    @staticmethod
    def create_cell(y):
        def foo(x):
            return lambda: x
        return foo(y).__closure__[0]

    @staticmethod
    def create_empty_cell():
        def foo():
            return lambda: x
            x = 1
        return foo().__closure__[0]

    def shell_substitute_run_ast_nodes(
                self,
                nodelist, cellname, interactivity='last_expr',
                compiler=compile, # ignored
                result=None,
            ):
        """
        Replaces get_ipython().run_ast_nodes
        """
        if not nodelist:
            return

        try:
            result_val = self.exec_ast_nodes(nodelist)
            if interactivity == 'last_expr':
                sys.displayhook(result_val)
            return False
        except:
            if result:
                result.error_before_exec = sys.exc_info()[1]
            self.shell.showtraceback()
            return True

    def exec_str(self, code):
        return self.exec_ast_nodes(ast.parse(code).body)

    def exec_ast_nodes(self, ast_nodes):
        container_code = ast.parse("""def _container1():
            def _container2():
                print(x)
            return _container2
            x = 1
        """)
        container1_body = container_code.body[0].body
        if isinstance(ast_nodes[-1], ast.Expr):
            ast_nodes[-1] = ast.Return(value=ast_nodes[-1].value)
        container1_body[0].body = ast_nodes
        container1_body[2].targets = [
            ast.Name(id=name, ctx=ast.Store())
                for name in self.locals_cells.keys()]

        # 1st call of python compiler gets the values of local vars
        compiled_code = compile(ast.fix_missing_locations(container_code),
            '<string>', 'exec')
        ld = {}
        eval(compiled_code, {}, ld)
        func = ld['_container1']()
        declared_vars = func.__code__.co_varnames

        if declared_vars:
            # There are assignments inside the user_code. Need to create new
            # cells in locals_cells and make sure the assignments target those cells
            for name in declared_vars:
                if name not in self.locals_cells:
                    self.locals_cells[name] = self.create_empty_cell()
            container1_body[0].body.insert(0, ast.Nonlocal(names=list(declared_vars)))
            container1_body[2].targets = [
                ast.Name(id=name, ctx=ast.Store())
                    for name in list(func.__code__.co_freevars) + list(declared_vars)]
            compiled_code = compile(ast.fix_missing_locations(container_code),
                '<string>', 'exec')
            ld = {}
            eval(compiled_code, {}, ld)
            func = ld['_container1']()
            assert len(func.__code__.co_varnames) == 0

        # Get the cells for the relevant vars
        closure = tuple(self.locals_cells[name] for name in func.__code__.co_freevars)
        res = types.FunctionType(func.__code__,
            self.globals_dict,
            func.__name__,
            func.__defaults__,
            closure)

        return res()
