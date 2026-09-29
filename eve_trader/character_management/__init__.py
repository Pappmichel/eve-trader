"""Character Management hub (docs/CHARACTER_MANAGEMENT_PLAN.md).

Tenant-facing character tools that sit on top of the ESI access system in
`eve_trader/esi_data/`: Character Info (phase 1), later Skills, Mail, ...
Each sub-tool is its own `tool_key` (`char_info`, `char_skills`, ...).

Dependency direction matters: this package imports `esi_data`, never the
other way round (`esi_data` names tools as strings and imports no tool
package). Raw ESI reads go through `esi_data.read_esi` / `is_shared` with the
sub-tool's own `tool_key`, so the Characters-page sharing matrix governs them
like every other tool.
"""
