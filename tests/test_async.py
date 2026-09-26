import asyncio
import gc
import sys
import warnings

import pytest

from jinja2 import ChainableUndefined
from jinja2 import DictLoader
from jinja2 import Environment
from jinja2 import Template
from jinja2.async_utils import aclosing
from jinja2.async_utils import auto_aiter
from jinja2.exceptions import TemplateNotFound
from jinja2.exceptions import TemplatesNotFound
from jinja2.exceptions import UndefinedError
from jinja2.nativetypes import NativeEnvironment


def test_basic_async():
    t = Template(
        "{% for item in [1, 2, 3] %}[{{ item }}]{% endfor %}", enable_async=True
    )

    async def func():
        return await t.render_async()

    rv = asyncio.run(func())
    assert rv == "[1][2][3]"


def test_await_on_calls():
    t = Template("{{ async_func() + normal_func() }}", enable_async=True)

    async def async_func():
        return 42

    def normal_func():
        return 23

    async def func():
        return await t.render_async(async_func=async_func, normal_func=normal_func)

    rv = asyncio.run(func())
    assert rv == "65"


def test_await_on_calls_normal_render():
    t = Template("{{ async_func() + normal_func() }}", enable_async=True)

    async def async_func():
        return 42

    def normal_func():
        return 23

    rv = t.render(async_func=async_func, normal_func=normal_func)
    assert rv == "65"


def test_await_and_macros():
    t = Template(
        "{% macro foo(x) %}[{{ x }}][{{ async_func() }}]{% endmacro %}{{ foo(42) }}",
        enable_async=True,
    )

    async def async_func():
        return 42

    async def func():
        return await t.render_async(async_func=async_func)

    rv = asyncio.run(func())
    assert rv == "[42][42]"


def test_async_blocks():
    t = Template(
        "{% block foo %}<Test>{% endblock %}{{ self.foo() }}",
        enable_async=True,
        autoescape=True,
    )

    async def func():
        return await t.render_async()

    rv = asyncio.run(func())
    assert rv == "<Test><Test>"


def test_async_generate():
    t = Template("{% for x in [1, 2, 3] %}{{ x }}{% endfor %}", enable_async=True)
    rv = list(t.generate())
    assert rv == ["1", "2", "3"]


def test_async_iteration_in_templates():
    t = Template("{% for x in rng %}{{ x }}{% endfor %}", enable_async=True)

    async def async_iterator():
        for item in [1, 2, 3]:
            yield item

    rv = list(t.generate(rng=async_iterator()))
    assert rv == ["1", "2", "3"]


def test_async_iteration_in_templates_extended():
    t = Template(
        "{% for x in rng %}{{ loop.index0 }}/{{ x }}{% endfor %}", enable_async=True
    )
    stream = t.generate(rng=auto_aiter(range(1, 4)))
    assert next(stream) == "0"
    assert "".join(stream) == "/11/22/3"


@pytest.fixture
def test_env_async():
    env = Environment(
        loader=DictLoader(
            dict(
                module="{% macro test() %}[{{ foo }}|{{ bar }}]{% endmacro %}",
                header="[{{ foo }}|{{ 23 }}]",
                o_printer="({{ o }})",
            )
        ),
        enable_async=True,
    )
    env.globals["bar"] = 23
    return env


class TestAsyncImports:
    def test_context_imports(self, test_env_async):
        t = test_env_async.from_string('{% import "module" as m %}{{ m.test() }}')
        assert t.render(foo=42) == "[|23]"
        t = test_env_async.from_string(
            '{% import "module" as m without context %}{{ m.test() }}'
        )
        assert t.render(foo=42) == "[|23]"
        t = test_env_async.from_string(
            '{% import "module" as m with context %}{{ m.test() }}'
        )
        assert t.render(foo=42) == "[42|23]"
        t = test_env_async.from_string('{% from "module" import test %}{{ test() }}')
        assert t.render(foo=42) == "[|23]"
        t = test_env_async.from_string(
            '{% from "module" import test without context %}{{ test() }}'
        )
        assert t.render(foo=42) == "[|23]"
        t = test_env_async.from_string(
            '{% from "module" import test with context %}{{ test() }}'
        )
        assert t.render(foo=42) == "[42|23]"

    def test_trailing_comma(self, test_env_async):
        test_env_async.from_string('{% from "foo" import bar, baz with context %}')
        test_env_async.from_string('{% from "foo" import bar, baz, with context %}')
        test_env_async.from_string('{% from "foo" import bar, with context %}')
        test_env_async.from_string('{% from "foo" import bar, with, context %}')
        test_env_async.from_string('{% from "foo" import bar, with with context %}')

    def test_exports(self, test_env_async):
        coro = test_env_async.from_string(
            """
            {% macro toplevel() %}...{% endmacro %}
            {% macro __private() %}...{% endmacro %}
            {% set variable = 42 %}
            {% for item in [1] %}
                {% macro notthere() %}{% endmacro %}
            {% endfor %}
            """
        )._get_default_module_async()
        m = asyncio.run(coro)
        assert asyncio.run(m.toplevel()) == "..."
        assert not hasattr(m, "__missing")
        assert m.variable == 42
        assert not hasattr(m, "notthere")

    def test_import_with_globals(self, test_env_async):
        t = test_env_async.from_string(
            '{% import "module" as m %}{{ m.test() }}', globals={"foo": 42}
        )
        assert t.render() == "[42|23]"

        t = test_env_async.from_string('{% import "module" as m %}{{ m.test() }}')
        assert t.render() == "[|23]"

    def test_import_with_globals_override(self, test_env_async):
        t = test_env_async.from_string(
            '{% set foo = 41 %}{% import "module" as m %}{{ m.test() }}',
            globals={"foo": 42},
        )
        assert t.render() == "[42|23]"

    def test_from_import_with_globals(self, test_env_async):
        t = test_env_async.from_string(
            '{% from "module" import test %}{{ test() }}',
            globals={"foo": 42},
        )
        assert t.render() == "[42|23]"


class TestAsyncIncludes:
    def test_context_include(self, test_env_async):
        t = test_env_async.from_string('{% include "header" %}')
        assert t.render(foo=42) == "[42|23]"
        t = test_env_async.from_string('{% include "header" with context %}')
        assert t.render(foo=42) == "[42|23]"
        t = test_env_async.from_string('{% include "header" without context %}')
        assert t.render(foo=42) == "[|23]"

    def test_choice_includes(self, test_env_async):
        t = test_env_async.from_string('{% include ["missing", "header"] %}')
        assert t.render(foo=42) == "[42|23]"

        t = test_env_async.from_string(
            '{% include ["missing", "missing2"] ignore missing %}'
        )
        assert t.render(foo=42) == ""

        t = test_env_async.from_string('{% include ["missing", "missing2"] %}')
        pytest.raises(TemplateNotFound, t.render)
        with pytest.raises(TemplatesNotFound) as e:
            t.render()

        assert e.value.templates == ["missing", "missing2"]
        assert e.value.name == "missing2"

        def test_includes(t, **ctx):
            ctx["foo"] = 42
            assert t.render(ctx) == "[42|23]"

        t = test_env_async.from_string('{% include ["missing", "header"] %}')
        test_includes(t)
        t = test_env_async.from_string("{% include x %}")
        test_includes(t, x=["missing", "header"])
        t = test_env_async.from_string('{% include [x, "header"] %}')
        test_includes(t, x="missing")
        t = test_env_async.from_string("{% include x %}")
        test_includes(t, x="header")
        t = test_env_async.from_string("{% include x %}")
        test_includes(t, x="header")
        t = test_env_async.from_string("{% include [x] %}")
        test_includes(t, x="header")

    def test_include_ignoring_missing(self, test_env_async):
        t = test_env_async.from_string('{% include "missing" %}')
        pytest.raises(TemplateNotFound, t.render)
        for extra in "", "with context", "without context":
            t = test_env_async.from_string(
                '{% include "missing" ignore missing ' + extra + " %}"
            )
            assert t.render() == ""

    def test_context_include_with_overrides(self, test_env_async):
        env = Environment(
            loader=DictLoader(
                dict(
                    main="{% for item in [1, 2, 3] %}{% include 'item' %}{% endfor %}",
                    item="{{ item }}",
                )
            )
        )
        assert env.get_template("main").render() == "123"

    def test_unoptimized_scopes(self, test_env_async):
        t = test_env_async.from_string(
            """
            {% macro outer(o) %}
            {% macro inner() %}
            {% include "o_printer" %}
            {% endmacro %}
            {{ inner() }}
            {% endmacro %}
            {{ outer("FOO") }}
        """
        )
        assert t.render().strip() == "(FOO)"

    def test_unoptimized_scopes_autoescape(self):
        env = Environment(
            loader=DictLoader({"o_printer": "({{ o }})"}),
            autoescape=True,
            enable_async=True,
        )
        t = env.from_string(
            """
            {% macro outer(o) %}
            {% macro inner() %}
            {% include "o_printer" %}
            {% endmacro %}
            {{ inner() }}
            {% endmacro %}
            {{ outer("FOO") }}
        """
        )
        assert t.render().strip() == "(FOO)"


class TestAsyncForLoop:
    def test_simple(self, test_env_async):
        tmpl = test_env_async.from_string("{% for item in seq %}{{ item }}{% endfor %}")
        assert tmpl.render(seq=list(range(10))) == "0123456789"

    def test_else(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for item in seq %}XXX{% else %}...{% endfor %}"
        )
        assert tmpl.render() == "..."

    def test_empty_blocks(self, test_env_async):
        tmpl = test_env_async.from_string(
            "<{% for item in seq %}{% else %}{% endfor %}>"
        )
        assert tmpl.render() == "<>"

    @pytest.mark.parametrize(
        "transform", [lambda x: x, iter, reversed, lambda x: (i for i in x), auto_aiter]
    )
    def test_context_vars(self, test_env_async, transform):
        t = test_env_async.from_string(
            "{% for item in seq %}{{ loop.index }}|{{ loop.index0 }}"
            "|{{ loop.revindex }}|{{ loop.revindex0 }}|{{ loop.first }}"
            "|{{ loop.last }}|{{ loop.length }}\n{% endfor %}"
        )
        out = t.render(seq=transform([42, 24]))
        assert out == "1|0|2|1|True|False|2\n2|1|1|0|False|True|2\n"

    def test_cycling(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in seq %}{{
            loop.cycle('<1>', '<2>') }}{% endfor %}{%
            for item in seq %}{{ loop.cycle(*through) }}{% endfor %}"""
        )
        output = tmpl.render(seq=list(range(4)), through=("<1>", "<2>"))
        assert output == "<1><2>" * 4

    def test_lookaround(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in seq -%}
            {{ loop.previtem|default('x') }}-{{ item }}-{{
            loop.nextitem|default('x') }}|
        {%- endfor %}"""
        )
        output = tmpl.render(seq=list(range(4)))
        assert output == "x-0-1|0-1-2|1-2-3|2-3-x|"

    def test_changed(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in seq -%}
            {{ loop.changed(item) }},
        {%- endfor %}"""
        )
        output = tmpl.render(seq=[None, None, 1, 2, 2, 3, 4, 4, 4])
        assert output == "True,False,True,True,False,True,True,False,False,"

    def test_scope(self, test_env_async):
        tmpl = test_env_async.from_string("{% for item in seq %}{% endfor %}{{ item }}")
        output = tmpl.render(seq=list(range(10)))
        assert not output

    def test_varlen(self, test_env_async):
        def inner():
            yield from range(5)

        tmpl = test_env_async.from_string(
            "{% for item in iter %}{{ item }}{% endfor %}"
        )
        output = tmpl.render(iter=inner())
        assert output == "01234"

    def test_noniter(self, test_env_async):
        tmpl = test_env_async.from_string("{% for item in none %}...{% endfor %}")
        pytest.raises(TypeError, tmpl.render)

    def test_recursive(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in seq recursive -%}
            [{{ item.a }}{% if item.b %}<{{ loop(item.b) }}>{% endif %}]
        {%- endfor %}"""
        )
        assert (
            tmpl.render(
                seq=[
                    dict(a=1, b=[dict(a=1), dict(a=2)]),
                    dict(a=2, b=[dict(a=1), dict(a=2)]),
                    dict(a=3, b=[dict(a="a")]),
                ]
            )
            == "[1<[1][2]>][2<[1][2]>][3<[a]>]"
        )

    def test_recursive_lookaround(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in seq recursive -%}
            [{{ loop.previtem.a if loop.previtem is defined else 'x' }}.{{
            item.a }}.{{ loop.nextitem.a if loop.nextitem is defined else 'x'
            }}{% if item.b %}<{{ loop(item.b) }}>{% endif %}]
        {%- endfor %}"""
        )
        assert (
            tmpl.render(
                seq=[
                    dict(a=1, b=[dict(a=1), dict(a=2)]),
                    dict(a=2, b=[dict(a=1), dict(a=2)]),
                    dict(a=3, b=[dict(a="a")]),
                ]
            )
            == "[x.1.2<[x.1.2][1.2.x]>][1.2.3<[x.1.2][1.2.x]>][2.3.x<[x.a.x]>]"
        )

    def test_recursive_depth0(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for item in seq recursive %}[{{ loop.depth0 }}:{{ item.a }}"
            "{% if item.b %}<{{ loop(item.b) }}>{% endif %}]{% endfor %}"
        )
        assert (
            tmpl.render(
                seq=[
                    dict(a=1, b=[dict(a=1), dict(a=2)]),
                    dict(a=2, b=[dict(a=1), dict(a=2)]),
                    dict(a=3, b=[dict(a="a")]),
                ]
            )
            == "[0:1<[1:1][1:2]>][0:2<[1:1][1:2]>][0:3<[1:a]>]"
        )

    def test_recursive_depth(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for item in seq recursive %}[{{ loop.depth }}:{{ item.a }}"
            "{% if item.b %}<{{ loop(item.b) }}>{% endif %}]{% endfor %}"
        )
        assert (
            tmpl.render(
                seq=[
                    dict(a=1, b=[dict(a=1), dict(a=2)]),
                    dict(a=2, b=[dict(a=1), dict(a=2)]),
                    dict(a=3, b=[dict(a="a")]),
                ]
            )
            == "[1:1<[2:1][2:2]>][1:2<[2:1][2:2]>][1:3<[2:a]>]"
        )

    def test_looploop(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for row in table %}
            {%- set rowloop = loop -%}
            {% for cell in row -%}
                [{{ rowloop.index }}|{{ loop.index }}]
            {%- endfor %}
        {%- endfor %}"""
        )
        assert tmpl.render(table=["ab", "cd"]) == "[1|1][1|2][2|1][2|2]"

    def test_reversed_bug(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for i in items %}{{ i }}"
            "{% if not loop.last %}"
            ",{% endif %}{% endfor %}"
        )
        assert tmpl.render(items=reversed([3, 2, 1])) == "1,2,3"

    def test_loop_errors(self, test_env_async):
        tmpl = test_env_async.from_string(
            """{% for item in [1] if loop.index
                                      == 0 %}...{% endfor %}"""
        )
        pytest.raises(UndefinedError, tmpl.render)
        tmpl = test_env_async.from_string(
            """{% for item in [] %}...{% else
            %}{{ loop }}{% endfor %}"""
        )
        assert tmpl.render() == ""

    def test_loop_filter(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for item in range(10) if item is even %}[{{ item }}]{% endfor %}"
        )
        assert tmpl.render() == "[0][2][4][6][8]"
        tmpl = test_env_async.from_string(
            """
            {%- for item in range(10) if item is even %}[{{
                loop.index }}:{{ item }}]{% endfor %}"""
        )
        assert tmpl.render() == "[1:0][2:2][3:4][4:6][5:8]"

    def test_scoped_special_var(self, test_env_async):
        t = test_env_async.from_string(
            "{% for s in seq %}[{{ loop.first }}{% for c in s %}"
            "|{{ loop.first }}{% endfor %}]{% endfor %}"
        )
        assert t.render(seq=("ab", "cd")) == "[True|True|False][False|True|False]"

    def test_scoped_loop_var(self, test_env_async):
        t = test_env_async.from_string(
            "{% for x in seq %}{{ loop.first }}"
            "{% for y in seq %}{% endfor %}{% endfor %}"
        )
        assert t.render(seq="ab") == "TrueFalse"
        t = test_env_async.from_string(
            "{% for x in seq %}{% for y in seq %}"
            "{{ loop.first }}{% endfor %}{% endfor %}"
        )
        assert t.render(seq="ab") == "TrueFalseTrueFalse"

    def test_recursive_empty_loop_iter(self, test_env_async):
        t = test_env_async.from_string(
            """
        {%- for item in foo recursive -%}{%- endfor -%}
        """
        )
        assert t.render(dict(foo=[])) == ""

    def test_call_in_loop(self, test_env_async):
        t = test_env_async.from_string(
            """
        {%- macro do_something() -%}
            [{{ caller() }}]
        {%- endmacro %}

        {%- for i in [1, 2, 3] %}
            {%- call do_something() -%}
                {{ i }}
            {%- endcall %}
        {%- endfor -%}
        """
        )
        assert t.render() == "[1][2][3]"

    def test_scoping_bug(self, test_env_async):
        t = test_env_async.from_string(
            """
        {%- for item in foo %}...{{ item }}...{% endfor %}
        {%- macro item(a) %}...{{ a }}...{% endmacro %}
        {{- item(2) -}}
        """
        )
        assert t.render(foo=(1,)) == "...1......2..."

    def test_unpacking(self, test_env_async):
        tmpl = test_env_async.from_string(
            "{% for a, b, c in [[1, 2, 3]] %}{{ a }}|{{ b }}|{{ c }}{% endfor %}"
        )
        assert tmpl.render() == "1|2|3"

    def test_recursive_loop_filter(self, test_env_async):
        t = test_env_async.from_string(
            """
        <?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          {%- for page in [site.root] if page.url != this recursive %}
          <url><loc>{{ page.url }}</loc></url>
          {{- loop(page.children) }}
          {%- endfor %}
        </urlset>
        """
        )
        sm = t.render(
            this="/foo",
            site={"root": {"url": "/", "children": [{"url": "/foo"}, {"url": "/bar"}]}},
        )
        lines = [x.strip() for x in sm.splitlines() if x.strip()]
        assert lines == [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            "<url><loc>/</loc></url>",
            "<url><loc>/bar</loc></url>",
            "</urlset>",
        ]

    def test_nonrecursive_loop_filter(self, test_env_async):
        t = test_env_async.from_string(
            """
        <?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          {%- for page in items if page.url != this %}
          <url><loc>{{ page.url }}</loc></url>
          {%- endfor %}
        </urlset>
        """
        )
        sm = t.render(
            this="/foo", items=[{"url": "/"}, {"url": "/foo"}, {"url": "/bar"}]
        )
        lines = [x.strip() for x in sm.splitlines() if x.strip()]
        assert lines == [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            "<url><loc>/</loc></url>",
            "<url><loc>/bar</loc></url>",
            "</urlset>",
        ]

    def test_bare_async(self, test_env_async):
        t = test_env_async.from_string('{% extends "header" %}')
        assert t.render(foo=42) == "[42|23]"

    def test_awaitable_property_slicing(self, test_env_async):
        t = test_env_async.from_string("{% for x in a.b[:1] %}{{ x }}{% endfor %}")
        assert t.render(a=dict(b=[1, 2, 3])) == "1"


def test_namespace_awaitable(test_env_async):
    async def _test():
        t = test_env_async.from_string(
            '{% set ns = namespace(foo="Bar") %}{{ ns.foo }}'
        )
        actual = await t.render_async()
        assert actual == "Bar"

    asyncio.run(_test())


def test_chainable_undefined_aiter():
    async def _test():
        t = Template(
            "{% for x in a['b']['c'] %}{{ x }}{% endfor %}",
            enable_async=True,
            undefined=ChainableUndefined,
        )
        rv = await t.render_async(a={})
        assert rv == ""

    asyncio.run(_test())


@pytest.fixture
def async_native_env():
    return NativeEnvironment(enable_async=True)


def test_native_async(async_native_env):
    async def _test():
        t = async_native_env.from_string("{{ x }}")
        rv = await t.render_async(x=23)
        assert rv == 23

    asyncio.run(_test())


def test_native_list_async(async_native_env):
    async def _test():
        t = async_native_env.from_string("{{ x }}")
        rv = await t.render_async(x=list(range(3)))
        assert rv == [0, 1, 2]

    asyncio.run(_test())


def test_getitem_after_filter():
    env = Environment(enable_async=True)
    env.filters["add_each"] = lambda v, x: [i + x for i in v]
    t = env.from_string("{{ (a|add_each(2))[1:] }}")
    out = t.render(a=range(3))
    assert out == "[3, 4]"


def test_getitem_after_call():
    env = Environment(enable_async=True)
    env.globals["add_each"] = lambda v, x: [i + x for i in v]
    t = env.from_string("{{ add_each(a, 2)[1:] }}")
    out = t.render(a=range(3))
    assert out == "[3, 4]"


# ---------------------------------------------------------------------------
# Closing async generators when streaming output is abandoned.
#
# When a caller does ``gen.aclose()`` on the result of
# ``Template.generate_async`` (for example because an HTTP client
# disconnected), every async generator created by the render -- the
# template ``root`` functions, ``block_`` functions, the ``auto_aiter``
# wrappers and async iterators passed in by the caller -- must be closed
# immediately.  They must not survive until garbage collection, which
# triggers ``ResourceWarning`` under runtimes such as trio.
# ---------------------------------------------------------------------------


class _gc_finalized_recorder:
    """Context manager that records async generators finalized through
    the garbage collector (via the ``sys.set_asyncgen_hooks`` finalizer)
    instead of being explicitly closed."""

    def __init__(self):
        self.names = []

    def __enter__(self):
        self._old = sys.get_asyncgen_hooks()
        sys.set_asyncgen_hooks(finalizer=self._finalizer)
        return self

    def __exit__(self, *exc):
        firstiter, finalizer = self._old
        sys.set_asyncgen_hooks(firstiter=firstiter, finalizer=finalizer)
        return False

    def _finalizer(self, agen, *args):
        self.names.append(agen.ag_code.co_name)


async def _async_items():
    yield 1
    yield 2
    yield 3


def _sync_generator_items():
    yield from (1, 2, 3)


def _recursive_items():
    return [
        {"v": 1, "c": [{"v": 2, "c": []}]},
        {"v": 3, "c": []},
    ]


_CLOSE_LOADER = DictLoader(
    {
        "simple.html": "{% for x in items %}[{{ x }}]{% endfor %}",
        "list.html": "{% for x in [1, 2, 3] %}[{{ x }}]{% endfor %}",
        "filter.html": "{% for x in items if x > 1 %}[{{ x }}]{% endfor %}",
        "extended.html": "{% for x in items %}[{{ loop.index }}:{{ x }}]{% endfor %}",
        "else.html": "{% for x in items %}[{{ x }}]{% else %}none{% endfor %}",
        "recursive.html": (
            "{% for item in items recursive %}"
            "({{ item.v }}{{ loop(item.c) }}){% endfor %}"
        ),
        "nested.html": (
            "{% for row in rows %}"
            "{% for y in row %}[{{ y }}]{% endfor %}|{% endfor %}"
        ),
        "include.html": '{% include "included.html" %}tail',
        "included.html": "[included]",
        "base.html": "{% block body %}[base]{% endblock %}tail",
        "extends.html": (
            '{% extends "base.html" %}{% block body %}[child]{% endblock %}'
        ),
        "self.html": "{% block foo %}[foo]{% endblock %}{{ self.foo() }}tail",
        "first.html": "{{ items|first }}tail",
        "map.html": "{% for x in items|map('string') %}{{ x }}{% endfor %}",
        "select.html": "{% for x in items|select('odd') %}{{ x }}{% endfor %}",
        "sum.html": "{{ items|sum }}tail",
    }
)


@pytest.mark.parametrize(
    ("name", "context_factory"),
    [
        ("list.html", None),
        ("simple.html", lambda: {"items": _async_items()}),
        ("simple.html", lambda: {"items": _sync_generator_items()}),
        ("filter.html", lambda: {"items": _async_items()}),
        ("extended.html", lambda: {"items": _async_items()}),
        ("else.html", lambda: {"items": _async_items()}),
        ("recursive.html", lambda: {"items": _recursive_items()}),
        (
            "nested.html",
            lambda: {"rows": [_async_items(), _async_items()]},
        ),
        ("include.html", None),
        ("extends.html", None),
        ("self.html", None),
        ("first.html", lambda: {"items": _async_items()}),
        ("map.html", lambda: {"items": _async_items()}),
        ("select.html", lambda: {"items": _async_items()}),
        ("sum.html", lambda: {"items": _async_items()}),
    ],
)
def test_generate_async_aclose_does_not_leak_generators(name, context_factory):
    async def run():
        env = Environment(enable_async=True, loader=_CLOSE_LOADER)
        context = context_factory() if context_factory is not None else {}
        recorder = _gc_finalized_recorder()
        with recorder:
            gen = env.get_template(name).generate_async(**context)
            await gen.__anext__()
            await gen.aclose()

            # Give garbage collection every chance to surface a leak.
            for _ in range(2):
                gc.collect()
                await asyncio.sleep(0)

        assert recorder.names == [], recorder.names

    asyncio.run(run())


def test_aclose_without_anext_does_not_leak():
    async def run():
        env = Environment(enable_async=True, loader=_CLOSE_LOADER)
        recorder = _gc_finalized_recorder()
        with recorder:
            gen = env.get_template("simple.html").generate_async(items=_async_items())
            await gen.aclose()
            gc.collect()
            await asyncio.sleep(0)
            gc.collect()
        assert recorder.names == [], recorder.names

    asyncio.run(run())


def test_aclose_runs_iterable_finalizer_immediately():
    closed = []

    async def guarded_items():
        try:
            yield 1
            yield 2
        finally:
            closed.append("closed")

    async def run():
        env = Environment(enable_async=True, loader=_CLOSE_LOADER)
        with _gc_finalized_recorder() as recorder:
            gen = env.get_template("simple.html").generate_async(
                items=guarded_items()
            )
            await gen.__anext__()
            await gen.aclose()
            gc.collect()
            await asyncio.sleep(0)
        assert closed == ["closed"]
        assert recorder.names == []

    asyncio.run(run())


def test_template_exception_still_closes_iterable():
    closed = []

    async def guarded_items():
        try:
            for x in range(5):
                yield x
        finally:
            closed.append("closed")

    async def run():
        env = Environment(enable_async=True)
        t = env.from_string(
            "{% for x in items %}{% if x == 2 %}{{ 1 / 0 }}"
            "{% endif %}[{{ x }}]{% endfor %}"
        )
        with _gc_finalized_recorder() as recorder:
            with pytest.raises(ZeroDivisionError):
                await t.render_async(items=guarded_items())
            gc.collect()
            await asyncio.sleep(0)
        assert closed == ["closed"]
        assert recorder.names == []

    asyncio.run(run())


def test_no_resource_warning_on_aclose():
    async def run():
        env = Environment(enable_async=True, loader=_CLOSE_LOADER)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            gen = env.get_template("simple.html").generate_async(items=_async_items())
            await gen.__anext__()
            await gen.aclose()
            gc.collect()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_full_renders_unchanged():
    expected = {
        "list.html": "[1][2][3]",
        "simple.html": "[1][2][3]",
        "filter.html": "[2][3]",
        "extended.html": "[1:1][2:2][3:3]",
        "else.html": "[1][2][3]",
        "recursive.html": "(1(2))(3)",
        "nested.html": "[1][2][3]|[1][2][3]|",
        "include.html": "[included]tail",
        "extends.html": "[child]tail",
        "self.html": "[foo][foo]tail",
        "first.html": "1tail",
        "map.html": "123",
        "select.html": "13",
        "sum.html": "6tail",
    }

    async def run():
        env = Environment(enable_async=True, loader=_CLOSE_LOADER)
        contexts = {
            "simple.html": {"items": [1, 2, 3]},
            "filter.html": {"items": [1, 2, 3]},
            "extended.html": {"items": [1, 2, 3]},
            "else.html": {"items": [1, 2, 3]},
            "recursive.html": {"items": _recursive_items()},
            "nested.html": {"rows": [[1, 2, 3], [1, 2, 3]]},
            "first.html": {"items": [1, 2, 3]},
            "map.html": {"items": [1, 2, 3]},
            "select.html": {"items": [1, 2, 3, 4]},
            "sum.html": {"items": [1, 2, 3]},
        }
        for name, want in expected.items():
            t = env.get_template(name)
            parts = []
            async for event in t.generate_async(**contexts.get(name, {})):
                parts.append(event)
            assert "".join(parts) == want, name

    asyncio.run(run())


def test_aclosing_context_manager():
    closed = []

    async def gen():
        try:
            yield 1
            yield 2
        finally:
            closed.append("closed")

    async def run_normal():
        async with aclosing(gen()) as g:
            assert await g.__anext__() == 1

    asyncio.run(run_normal())
    assert closed == ["closed"]

    async def run_error():
        with pytest.raises(ValueError):
            async with aclosing(gen()) as g:
                await g.__anext__()
                raise ValueError("boom")

    del closed[:]
    asyncio.run(run_error())
    assert closed == ["closed"]


def test_auto_aiter_closes_wrapped_async_iterator():
    closed = []

    async def source():
        try:
            yield "a"
            yield "b"
        finally:
            closed.append("closed")

    async def run():
        wrapper = auto_aiter(source())
        assert await wrapper.__anext__() == "a"
        await wrapper.aclose()

    asyncio.run(run())
    assert closed == ["closed"]


def test_auto_aiter_leaves_sync_iterables_working():
    async def run():
        result = []
        async with aclosing(auto_aiter([1, 2, 3])) as g:
            async for x in g:
                result.append(x)
        return result

    assert asyncio.run(run()) == [1, 2, 3]

