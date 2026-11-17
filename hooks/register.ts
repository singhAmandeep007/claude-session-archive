/**
 * Session Archive mod: answers /session-archive:save and /session-archive:import
 * itself, so they cost zero tokens and show their report as a normal command output.
 *
 * Without mods (Claude Code before v2.1.287) the UserPromptExpansion hook in
 * hooks.json answers the same commands instead, and Claude Code shows that answer
 * as "A hook blocked your prompt". Both paths run `scripts/archive.py`.
 *
 * Example: `/session-archive:save all`
 *   -> "session-archive backfilled 49 sessions into ~/claude-archive"
 */
import type { EngineInterface, PluginOptions, Register } from 'claude-code'

// -----------------------------------------------------------------------------
// Utilities
// -----------------------------------------------------------------------------

const TIMEOUT_MS = 10 * 60 * 1000 // `save all` on a long history; the engine's maximum
const PYTHONS = ['python3', 'python'] // Windows installs often have only `python`

/** The configured archive folder, or undefined to let archive.py resolve its default. */
function archiveDir(options: PluginOptions): string | undefined {
  const dir = options.archive_dir
  return typeof dir === 'string' && dir.trim() ? dir.trim() : undefined
}

// -----------------------------------------------------------------------------
// Business logic
// -----------------------------------------------------------------------------

/**
 * Run `archive.py command` with the session's details on stdin; return its report.
 *
 * Example: answer($, opts, 'session-archive:save', '') -> "session-archive saved 3 new messages ..."
 */
async function answer($: EngineInterface, options: PluginOptions, command: string, args: string): Promise<string> {
  const payload = JSON.stringify({
    command_name: command,
    command_args: args,
    session_id: await $.session.id(),
    cwd: await $.session.cwd(),
  })
  const script = `${$.plugin.root}/scripts/archive.py`
  const dir = archiveDir(options)
  const argv = [script, ...(dir ? ['--archive-dir', dir] : []), 'command']

  let lastError = ''
  for (const python of PYTHONS) {
    try {
      const ran = await $.process.run([python, ...argv], { stdin: payload, timeoutMs: TIMEOUT_MS })
      if (ran.exitCode === 0 && ran.stdout.trim()) return ran.stdout.trim()
      lastError = ran.stderr.trim() || `${python} exited with ${ran.exitCode}`
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err) // not installed, or timed out
    }
  }
  return `failed: ${lastError}. It needs Python 3 on PATH (python3 or python).`
}

/** A hook that throws still answers, so the command never falls through to a model turn. */
function failed(_$: unknown, e: { command: string }) {
  return { text: `/${e.command} failed in the mod; details in the debug log (claude --debug)` }
}

export const register: Register = (on, options) => {
  // Answer both commands here so they never expand into a prompt for the model.
  on('command.run', { command: 'session-archive:save' }, async ($, e) =>
    ({ text: await answer($, options, e.command, e.args.trim()) })).catch(failed)
  on('command.run', { command: 'session-archive:import' }, async ($, e) =>
    ({ text: await answer($, options, e.command, e.args.trim()) })).catch(failed)
}
