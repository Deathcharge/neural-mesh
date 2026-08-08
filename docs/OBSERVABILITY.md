# Observability events

Observability is opt-in and application-owned. Pass asynchronous `ConsensusObserver` sinks to
`ConsensusEngine`; after persistence completes, every successful council operation emits one
`ConsensusEvent`. The core package does not install or configure an OpenTelemetry SDK, exporter,
network client, or global tracer.

```python
from neural_mesh import ConsensusEngine, ConsensusEvent


class MyObserver:
    async def record(self, event: ConsensusEvent) -> None:
        await my_event_sink.write(event.to_dict())


engine = ConsensusEngine(
    providers,
    observers=[MyObserver()],
    observer_timeout_seconds=1.0,
)
```

The stable event name is `neural_mesh.consensus.completed`; its schema is
`neural-mesh-consensus-event/v1`. Up to 16 observers can be configured, and each callback has an
independent timeout from 0.01 to 30 seconds. Sink errors and timeouts are redacted and do not change
the consensus result. Caller cancellation still propagates.

## OpenTelemetry mapping

`event.attributes` can be attached to an application-created span or emitted through an
OpenTelemetry event/log adapter. It uses the current GenAI semantic-convention attributes where they
fit:

- `gen_ai.operation.name=invoke_workflow`;
- `gen_ai.workflow.name=neural-mesh.consensus`;
- `gen_ai.request.max_tokens`; and
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` only when every selected provider
  completed and reported tokens.

Council-specific measurements use the `neural_mesh.*` namespace: task digest, duration, provider
outcome counts, agreement, consensus reached, usage completeness, decimal cost text, and persistence
status. `error.type` is set to the low-cardinality `neural_mesh.usage.write_failed` only when optional
usage persistence fails.

The library emits an event record rather than creating a span because the application owns trace
context, sampling, lifecycle, and exporter policy. OpenTelemetry recommends spans for operations
with meaningful duration and events for named point-in-time outcomes; an adapter can therefore put
these completion attributes on the active council span or emit the named event.

## Privacy and cardinality

Default events never contain task text, prompts, responses, provider/model names, exception messages,
or credentials. The task is represented by SHA-256. This is pseudonymous, not anonymous: predictable
task names can be guessed. Attribute values are intentionally aggregate and bounded, but a unique
task digest can still be high cardinality; omit it in an adapter when the backend or billing model
requires lower cardinality.

Observers run in the request path. Keep them cancellation-cooperative and non-blocking, and configure
their own bounded queues/export policies if they send data remotely.
