#
#  Copyright (c) 2023-2024 - Restate Software, Inc., Restate GmbH
#
#  This file is part of the Restate SDK for Python,
#  which is released under the MIT license.
#
#  You can find a copy of the license in file LICENSE in the root
#  directory of this repository or package, or at
#  https://github.com/restatedev/sdk-typescript/blob/main/LICENSE
#

# pylint: disable=R0917
"""
This module contains the definition of the Handler class,
which is used to define the handlers for the services.
"""

from dataclasses import dataclass
from inspect import Signature
from typing import Any, Awaitable, Callable, Generic, Literal, Optional, TypeVar

from restate.exceptions import TerminalError
from restate.serde import GeneralSerde, PydanticBaseModel, PydanticJsonSerde, Serde

I = TypeVar('I')
O = TypeVar('O')
T = TypeVar('T')

# we will use this symbol to store the handler in the function
RESTATE_UNIQUE_HANDLER_SYMBOL = str(object())

@dataclass
class ServiceTag:
    """
    This class is used to identify the service.
    """
    kind: Literal["object", "service", "workflow"]
    name: str

@dataclass
class TypeHint(Generic[T]):
    """
    Represents a type hint.
    """
    annotation: Optional[T] = None
    is_pydantic: bool = False

@dataclass
class HandlerIO(Generic[I, O]):
    """
    Represents the input/output configuration for a handler.

    Attributes:
        accept (str): The accept header value for the handler.
        content_type (str): The content type header value for the handler.
    """
    accept: str
    content_type: str
    input_serde: Serde[I]
    output_serde: Serde[O]
    input_type: Optional[TypeHint[I]] = None
    output_type: Optional[TypeHint[O]] = None

def is_pydantic(annotation) -> bool:
    """
    Check if an object is a Pydantic model.
    """
    try:
        return issubclass(annotation, PydanticBaseModel)
    except TypeError:
        # annotation is not a class or a type
        return False


def extract_io_type_hints(handler_io: HandlerIO[I, O], signature: Signature):
    """
    Augment handler_io with additional information about the input and output types.

    This function has a special check for Pydantic models when these are provided.
    This method will inspect the signature of an handler and will look for
    the input and the return types of a function, and will:
    * capture any Pydantic models (to be used later at discovery)
    * replace the default json serializer (is unchanged by a user) with a Pydantic serde
    """
    annotation = list(signature.parameters.values())[-1].annotation
    handler_io.input_type = TypeHint(annotation=annotation, is_pydantic=False)

    if is_pydantic(annotation):
        handler_io.input_type.is_pydantic = True
        if isinstance(handler_io.input_serde, GeneralSerde): # type: ignore
            handler_io.input_serde = PydanticJsonSerde(annotation)

    annotation = signature.return_annotation
    handler_io.output_type = TypeHint(annotation=annotation, is_pydantic=False)

    if is_pydantic(annotation):
        handler_io.output_type.is_pydantic=True
        if isinstance(handler_io.output_serde, GeneralSerde): # type: ignore
            handler_io.output_serde = PydanticJsonSerde(annotation)

@dataclass
class Handler(Generic[I, O]):
    """
    Represents a handler for a service.
    """
    service_tag: ServiceTag
    handler_io: HandlerIO[I, O]
    kind: Optional[Literal["exclusive", "shared", "workflow"]]
    name: str
    fn: Callable[[Any, I], Awaitable[O]] | Callable[[Any], Awaitable[O]]
    arity: int


# disable too many arguments warning
# pylint: disable=R0913

def make_handler(service_tag: ServiceTag,
                 handler_io: HandlerIO[I, O],
                 name: str | None,
                 kind: Optional[Literal["exclusive", "shared", "workflow"]],
                 wrapped: Any,
                 signature: Signature) -> Handler[I, O]:
    """
    Factory function to create a handler.

    Note:
        This function mutates the `handler_io` parameter by updating its type hints
        and serdes based on the function signature. Callers should be aware that the
        passed `handler_io` instance will be modified.
    """
    # try to deduce the handler name
    handler_name = name
    if not handler_name:
        handler_name = wrapped.__name__
    if not handler_name:
        raise ValueError("Handler name must be provided")

    if len(signature.parameters) == 0:
        raise ValueError("Handler must have at least one parameter")

    arity = len(signature.parameters)
    extract_io_type_hints(handler_io, signature) # mutates handler_io

    handler = Handler[I, O](service_tag,
                            handler_io,
                            kind,
                            handler_name,
                            wrapped,
                            arity)

    vars(wrapped)[RESTATE_UNIQUE_HANDLER_SYMBOL] = handler
    return handler

def handler_from_callable(wrapper: Callable[[Any, I], Awaitable[O]]) -> Handler[I, O]:
    """
    Get the handler from the callable.
    """
    try:
        return vars(wrapper)[RESTATE_UNIQUE_HANDLER_SYMBOL]
    except KeyError:
        raise ValueError("Handler not found") # pylint: disable=raise-missing-from

async def invoke_handler(handler: Handler[I, O], ctx: Any, in_buffer: bytes) -> bytes:
    """
    Invoke the handler with the given context and input.
    """
    if handler.arity == 2:
        try:
            in_arg = handler.handler_io.input_serde.deserialize(in_buffer) # type: ignore
        except Exception as e:
            raise TerminalError(message=f"Unable to parse an input argument. {e}") from e
        out_arg = await handler.fn(ctx, in_arg) # type: ignore [call-arg, arg-type]
    else:
        out_arg = await handler.fn(ctx) # type: ignore [call-arg]
    out_buffer = handler.handler_io.output_serde.serialize(out_arg) # type: ignore
    return bytes(out_buffer)
