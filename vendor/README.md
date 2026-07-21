# Private integration wheels

The public Mnemome source does not redistribute the internal Lotte Agent
package. An authorized deployment may place a locally built
`lotte_agent-*.whl` in this directory before building the service image.

Set `MNEMOME_REQUIRE_LOTTE_AGENT=1` in the deployment environment to make a
missing wheel fail the image build instead of leaving the optional demo runtime
disabled. Wheel files are intentionally ignored by Git.
