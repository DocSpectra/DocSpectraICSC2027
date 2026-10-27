# Sprint 11 bait image (test only, never used for sessions): decoy global and
# system Codex settings and instructions, each with a unique marker.
FROM docspectra/codex-editor:v1
USER root
RUN mkdir -p /etc/codex/skills/bait-sys /home/editor/.codex /home/editor/.agents/skills/bait-home \
 && printf 'developer_instructions = "BAITMARK-G system config"\nmodel = "bait-model-g"\n' > /etc/codex/config.toml \
 && printf 'developer_instructions = "BAITMARK-R requirements"\n' > /etc/codex/requirements.toml \
 && printf '%s\n' '---' 'name: bait-sys' 'description: BAITMARK-H system skill' '---' 'BAITMARK-H body' > /etc/codex/skills/bait-sys/SKILL.md \
 && printf 'developer_instructions = "BAITMARK-D home config"\nmodel = "bait-model-d"\n' > /home/editor/.codex/config.toml \
 && printf 'BAITMARK-E home AGENTS.md\n' > /home/editor/.codex/AGENTS.md \
 && printf '%s\n' '---' 'name: bait-home' 'description: BAITMARK-F home skill' '---' 'BAITMARK-F body' > /home/editor/.agents/skills/bait-home/SKILL.md \
 && chown -R 10001:10001 /home/editor
USER 10001:10001
