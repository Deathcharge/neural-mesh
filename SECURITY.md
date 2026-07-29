# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to `support@samsarix.com`. Include the affected
version or commit, a minimal reproduction, impact, and any relevant deployment assumptions. Do not
send API keys, credentials, private prompts, model responses, customer data, or destructive proof of
concepts.

If GitHub private vulnerability reporting is enabled for the repository, it is also an acceptable
channel. Please do not open a public issue for an uncoordinated vulnerability disclosure.

Samsarix LLC will confirm receipt as soon as practicable, investigate against the documented trust
boundaries, and coordinate remediation and disclosure with the reporter. Reports about downstream
provider SDKs or hosted applications may be redirected when the vulnerable control is outside this
package.

## Supported versions

Until the first public package release, security fixes target the latest commit on the default branch.
After publication, the latest released minor line will receive security fixes; older lines may require
upgrading unless Samsarix announces otherwise.

## Security boundaries

`neural-mesh` does not load credentials, contact providers, retry calls, expose a network listener, or
persist data by default. Applications supply provider adapters and are responsible for authentication,
authorization, provider retention policy, rate limiting, and deployment-specific spend controls.
Agreement between model responses is not evidence that an answer is safe or factually correct.
