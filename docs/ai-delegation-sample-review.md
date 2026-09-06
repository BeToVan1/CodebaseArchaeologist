# Delegation samples: provisional review

Source: owner-pasted provider records, using the revised local-behavior prompt
and `@cf/meta/llama-3.3-70b-instruct-fp8-fast`. This is an assistant assessment,
not independent human review or a semantic acceptance pass.

## Conditional execution

Input hash: `0de555ab797a7fef7cfc06da61a66f70a6e6b9c9575b8add43d92d4171b8dfe7`.

The execution section correctly described the None guard and delegated call.
The behavior section incorrectly described True/False as save success/failure.
False is returned only for None. Otherwise the delegated return value is ignored,
and True follows normal completion even if save returns False. An exception
propagates instead of becoming False. The sample therefore fails the provisional
behavior assessment; a correct section does not cancel a contradictory summary.

## Dependency injection

Input hash: `da8bb83b8ff6d57bbe24b58a82566843e44296da890728203eb4ab1efb8fdaeb`.

The response correctly described delegation and returning the delegated result.
It did not invent a concrete database. It missed the visible structural choice
of receiving the repository from the caller rather than constructing it locally.
Speculation about a larger order-management system was unnecessary. Runtime
effects and exception behavior remain unresolved without the implementation.

## Local follow-up

The prompt now requests actual return conditions, treatment of delegated return
values, and normal-completion versus operation-success distinctions. It asks for
visible dependency boundaries instead of speculative surrounding architecture.
No case-specific answer is embedded in the prompt. Offline tests check request
wiring and unchanged limits, not model compliance. This revision has not been
deployed or sampled. Preserve earlier records; new calls need explicit approval.
