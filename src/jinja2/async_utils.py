import inspect
import typing as t
from functools import WRAPPER_ASSIGNMENTS
from functools import wraps

from .utils import _PassArg
from .utils import pass_eval_context

V = t.TypeVar("V")


def async_variant(normal_func):  # type: ignore
    def decorator(async_func):  # type: ignore
        pass_arg = _PassArg.from_obj(normal_func)
        need_eval_context = pass_arg is None

        if pass_arg is _PassArg.environment:

            def is_async(args: t.Any) -> bool:
                return t.cast(bool, args[0].is_async)

        else:

            def is_async(args: t.Any) -> bool:
                return t.cast(bool, args[0].environment.is_async)

        # Take the doc and annotations from the sync function, but the
        # name from the async function. Pallets-Sphinx-Themes
        # build_function_directive expects __wrapped__ to point to the
        # sync function.
        async_func_attrs = ("__module__", "__name__", "__qualname__")
        normal_func_attrs = tuple(set(WRAPPER_ASSIGNMENTS).difference(async_func_attrs))

        @wraps(normal_func, assigned=normal_func_attrs)
        @wraps(async_func, assigned=async_func_attrs, updated=())
        def wrapper(*args, **kwargs):  # type: ignore
            b = is_async(args)

            if need_eval_context:
                args = args[1:]

            if b:
                return async_func(*args, **kwargs)

            return normal_func(*args, **kwargs)

        if need_eval_context:
            wrapper = pass_eval_context(wrapper)

        wrapper.jinja_async_variant = True  # type: ignore[attr-defined]
        return wrapper

    return decorator


_common_primitives = {int, float, bool, str, list, dict, tuple, type(None)}


async def auto_await(value: t.Union[t.Awaitable["V"], "V"]) -> "V":
    # Avoid a costly call to isawaitable
    if type(value) in _common_primitives:
        return t.cast("V", value)

    if inspect.isawaitable(value):
        return await t.cast("t.Awaitable[V]", value)

    return t.cast("V", value)


class aclosing:
    """Async context manager that calls :meth:`aclose` on the async
    iterator when the block is exited for any reason.

    Equivalent to :func:`contextlib.aclosing`, which is only available
    in Python 3.10.  Keeping a copy here allows supporting Python 3.7.
    """

    def __init__(self, thing: "t.AsyncIterator[t.Any]") -> None:
        self.thing = thing

    async def __aenter__(self) -> "t.AsyncIterator[t.Any]":
        return self.thing

    async def __aexit__(self, *exc_info: t.Any) -> None:
        await self.thing.aclose()


async def auto_aiter(
    iterable: "t.Union[t.AsyncIterable[V], t.Iterable[V]]",
) -> "t.AsyncIterator[V]":
    if hasattr(iterable, "__aiter__"):
        # The async iterable owns its own resources, so close its
        # iterator if iteration is abandoned early.  Plain sync
        # iterables do not have an ``aclose`` method.
        iterator = t.cast("t.AsyncIterator[V]", iterable.__aiter__())

        async with aclosing(iterator):
            async for item in iterator:
                yield item
    else:
        for item in iterable:
            yield item


async def auto_to_list(
    value: "t.Union[t.AsyncIterable[V], t.Iterable[V]]",
) -> t.List["V"]:
    async with aclosing(auto_aiter(value)) as it:
        return [x async for x in it]
