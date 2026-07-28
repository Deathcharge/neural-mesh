# Release process

Releases are built, attested, and published by `.github/workflows/release.yml`. The workflow contains
no long-lived package-index credential and does not run on pull requests or manual dispatch.

## One-time owner setup

1. Claim or create the `neural-mesh` project on PyPI. As of 2026-07-28, the canonical project and JSON
   endpoints returned 404, which suggests the name was unregistered at that moment but does not reserve
   it.
2. In the GitHub repository, create an environment named `pypi`. Add trusted maintainers as required
   reviewers and restrict deployment to protected release tags where the repository plan supports it.
3. Configure the PyPI trusted publisher with:
   - owner: `Deathcharge`;
   - repository: `neural-mesh`;
   - workflow: `release.yml`;
   - environment: `pypi`.
4. Require the `CI` workflow on the default branch and restrict direct pushes and tag creation to
   trusted maintainers.

If the repository moves to a Samsarix GitHub organization, update the repository URLs and PyPI trusted
publisher together before releasing. A stale publisher identity must not remain authorized.

## Release checklist

1. Confirm the version in `pyproject.toml` and `neural_mesh/__init__.py` match.
2. Move the intended changelog entries under that version and use an ISO release date.
3. Run the checks in `CONTRIBUTING.md` from a clean environment.
4. Review `LICENSE`, `SECURITY.md`, runtime dependencies, provider-cost behavior, and the complete diff.
5. Merge through a pull request and wait for all required CI jobs.
6. Create a signed tag named exactly `v<version>`, such as `v0.2.0`, from the verified default-branch
   commit.
7. Create a non-prerelease GitHub release for that tag. Publishing the release starts the workflow;
   the workflow rejects a tag that does not match package metadata.
8. Approve the protected `pypi` environment only after reviewing the built artifact and provenance job.
9. Verify the PyPI project metadata, provenance, hashes, and installation in a new environment.

Prereleases are intentionally not uploaded by this workflow. Add an explicit, reviewed TestPyPI or
prerelease process rather than weakening the production publisher trigger.

## Rollback and incident handling

PyPI files are immutable and releases cannot be overwritten. If a bad release is published, yank it,
publish a corrected higher version, document the impact, and follow `SECURITY.md` when confidentiality,
integrity, availability, or credential exposure is involved.
