from slcs.parsers import mask_code, parse_units
from slcs.textio import split_lines


def units(text, lang):
    return {u.name: (u.kind, u.start_line, u.end_line) for u in parse_units(text, lang).units}


def test_python_functions_methods_decorators_and_comments():
    src = '''import os

# helper comment
def top(a):
    return a


@decorator
class Svc:
    """doc"""
    x = 1

    @staticmethod
    def build():
        return 1

    async def run(self):
        return 2
'''
    u = units(src, "python")
    assert u["top"] == ("function", 3, 5)                    # comment line included
    assert u["Svc"][0] == "class" and u["Svc"][1] == 8       # decorator included
    assert u["Svc.build"] == ("method", 13, 15)              # decorator included
    assert u["Svc.run"][0] == "method"
    assert u["Svc"][2] < u["Svc.build"][1]                   # class header does not overlap members


def test_python_syntax_error_falls_back_with_warning():
    r = parse_units("def broken(:\n  pass\n", "python")
    assert r.units == [] and "parse failed" in r.warning


def test_python_class_without_methods_is_one_unit():
    assert units("class E(Exception):\n    pass\n", "python") == {"E": ("class", 1, 2)}


def test_javascript_function_arrow_class_methods():
    src = '''// retry wrapper
export async function apiFetch(url) {
  return fetch(url);
}
export const refresh = async (x) => {
  return 1;
};
const quick = (a) => a + 1;
export class ApiClient {
  constructor(base) { this.base = base; }
  async get(path) {
    if (path) { return 1; }
    return fetch(path).then(r => {
      return r;
    });
  }
  static of(x) {
    return new ApiClient(x);
  }
}
'''
    u = units(src, "javascript")
    assert u["apiFetch"] == ("function", 1, 4)
    assert u["refresh"] == ("function", 5, 7)
    assert u["quick"] == ("function", 8, 8)
    assert u["ApiClient.constructor"][0] == "method"
    assert u["ApiClient.get"] == ("method", 11, 16)
    assert u["ApiClient.of"][0] == "method"          # 'of' is a legal method name
    assert "if" not in "".join(u)                    # control flow never becomes a symbol


def test_typescript_interface_and_enum():
    u = units("export interface User {\n  id: number;\n}\nexport enum Role {\n  A,\n  B,\n}\n", "typescript")
    assert u["User"][0] == "interface" and u["Role"][0] == "enum"


def test_braces_inside_strings_and_comments_do_not_break_matching():
    src = 'function f() {\n  const s = "}";\n  // }\n  /* { */\n  return s;\n}\nfunction g() {\n  return 1;\n}\n'
    u = units(src, "javascript")
    assert u["f"] == ("function", 1, 6) and u["g"] == ("function", 7, 9)


def test_go_methods_and_structs():
    src = "package x\ntype Stock struct {\n  a int\n}\n// Reserve things\nfunc (s *Stock) Reserve(id string) (int, error) {\n  return 0, nil\n}\nfunc Helper() {}\n"
    u = units(src, "go")
    assert u["Stock"][0] == "struct"
    assert u["Stock.Reserve"] == ("method", 5, 8)
    assert u["Helper"] == ("function", 9, 9)


def test_java_methods_skip_abstract_declarations():
    src = "public class A {\n    /** doc */\n    public int f(int x) {\n        if (x > 0) { return x; }\n        return 0;\n    }\n    void g();\n}\n"
    u = units(src, "java")
    assert u["A.f"] == ("method", 2, 6)
    assert "A.g" not in u and "g" not in u


def test_rust_lifetimes_and_chars_do_not_confuse_scanner():
    src = "pub struct M { a: u8 }\nimpl M {\n    pub fn run(&self) -> Result<(), E> {\n        let c = 'x'; let r: &'a str = \"}\";\n        Ok(())\n    }\n}\nfn main() {}\n"
    u = units(src, "rust")
    assert u["M.run"][0] == "method" and u["main"] == ("function", 8, 8)


def test_csharp_attribute_included_and_namespace_transparent():
    src = "namespace A {\n  public class Svc {\n    [HttpGet]\n    public async Task<int> GetAsync(int id) {\n      return await Foo(id);\n    }\n  }\n}\n"
    u = units(src, "csharp")
    assert u["Svc.GetAsync"] == ("method", 3, 6)


def test_php_class_and_method():
    u = units("<?php\nclass Pay {\n  public function charge($a) {\n    return 1;\n  }\n}\n", "php")
    assert u["Pay.charge"][0] == "method"


def test_c_function_and_struct():
    u = units("struct point { int x; };\nstatic int add(int a, int b)\n{\n    return a + b;\n}\n", "c")
    assert u["point"][0] == "struct" and u["add"] == ("function", 2, 5)


def test_cpp_class_method():
    u = units("class Foo {\npublic:\n  int bar(int x) {\n    return x;\n  }\n};\n", "cpp")
    assert any(n.endswith("bar") for n in u)


def test_sql_statements_dollar_quotes_and_string_semicolons():
    src = "-- users\nCREATE TABLE users (id int);\nINSERT INTO users VALUES ('a;b');\nCREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;\n"
    u = units(src, "sql")
    assert u["users"] == ("table", 1, 2)
    assert u["INSERT users"][0] == "statement"
    assert u["f"][0] == "function" and u["f"][1] == 4 == u["f"][2]


def test_unsupported_language_returns_no_units():
    assert parse_units("a: 1", "config").units == []


def test_mask_preserves_length_and_newlines():
    src = 'a = "x\\"y" // c\nb = `t\nu`\n'
    m = mask_code(src, "javascript")
    assert len(m) == len(src) and m.count("\n") == src.count("\n")


def test_all_unit_ranges_valid_for_sample_repository(repo_copy):
    from slcs.scanner import scan_repository, Settings
    for f in scan_repository(repo_copy, Settings()).files:
        text = open(f.abs_path, encoding="utf-8").read()
        n = len(split_lines(text))
        for u in parse_units(text, f.language).units:
            assert 1 <= u.start_line <= u.end_line <= n, (f.rel_path, u)
