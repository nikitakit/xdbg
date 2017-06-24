import byteplay3 as bp
import inspect

# %% base class for breakpoint tables

class BaseBreakpointTable:
    def __init__(self):
        self.counter = 0

    def new_breakpoint(self, func, lineno):
        print("Created breakpoint {}:{}".format(func.__name__, lineno))
        num = self.counter
        self.counter += 1
        return num

    def __call__(self, num, module_name, locals_dict):
        """
        Called whenever a breakpoint is hit.
        Returns a tuple (do_return, return_value)
        """
        print("Breakpoint", num, "called with locals", locals_dict)
        return False, None

# %% Allows tables created anywhere to be used with this module
# (this makes it easier to extend)

table_counter = 0
table_refs = {}

def get_table_ref(table):
    if table in table_refs:
        return table_refs[table]

    global table_counter
    table_num = table_counter
    name = '_t{}'.format(table_num)
    globals()[name] = table
    table_refs[table] = (__name__, name)
    return table_refs[table]

# %%

# table = BaseBreakpointTable()

# %%

def add_breakpoint_at(table, func, code, inject_index):
    # Hook location found, now allocate a breakpoint number
    lineno = func.__code__.co_firstlineno
    for prev_index in range(inject_index, -1, -1):
        opcode, arg = code[prev_index]
        if opcode == bp.SetLineno:
            lineno = arg
            break

    breakpoint_num = table.new_breakpoint(func, lineno)
    do_hook_module, do_hook_name = get_table_ref(table)

    continue_label = bp.Label()

    code[inject_index:inject_index] = [
        (bp.LOAD_CONST, 0),
        (bp.LOAD_CONST, (do_hook_name,)),
        (bp.IMPORT_NAME, do_hook_module),
        (bp.IMPORT_FROM, do_hook_name),
        # don't store the result in a variable, to avoid namespace pollution
        (bp.LOAD_CONST, breakpoint_num),
        (bp.LOAD_GLOBAL, '__name__'),
        (bp.LOAD_GLOBAL, 'locals'),
        (bp.CALL_FUNCTION, 0),
        (bp.CALL_FUNCTION, 3),
        # Now the result of table(breakpoint_num, __name__, locals()) is on the
        # stack. This result is a tuple (do_return, return_value).
        (bp.UNPACK_SEQUENCE, 2),
        (bp.POP_JUMP_IF_FALSE, continue_label),
        (bp.RETURN_VALUE, None),
        (continue_label, None),
        (bp.POP_TOP, None), # pop unused return_value
    ]

    return breakpoint_num

def add_breakpoint(table, func, lineno=None):
    b = bp.Code.from_code(func.__code__)

    inject_index = 0
    if lineno is None:
        # Try to inject right after the first SetLineno
        have_code = False
        try:
            b.code[0][0]
            have_code = True
        except IndexError:
            pass

        if have_code:
            if b.code[0][0] == bp.SetLineno:
                inject_index = 1
    else:
        for i, (opcode, arg) in enumerate(b.code):
            if opcode == bp.SetLineno and arg == lineno:
                inject_index = i
                break
        else:
            raise ValueError("Could not find line number {}".format(lineno))

    num = add_breakpoint_at(table, func, b.code, inject_index)

    func.__code__ = b.to_code()
    return num

def materialize_breakpoints(table, func):
    b = bp.Code.from_code(func.__code__)
    i = 0
    while i < len(b.code):
        opcode, arg = b.code[i]
        if opcode == bp.LOAD_GLOBAL and arg == '___xdbg_breakpoint_here':
            assert i+1 < len(b.code), "breakpoint flag is not the last opcode"
            assert b.code[i+1][0] == bp.POP_TOP, "breakpoint flag should be followed by POP_TOP"
            del b.code[i:i+2]
            add_breakpoint_at(table, func, b.code, i)
        else:
            i += 1

    func.__code__ = b.to_code()
