/**
 * Tests for the mod (hooks/register.ts): it answers both slash commands by running
 * scripts/archive.py, and never lets a command fall through to the model.
 *
 * Run: claude plugin test .
 * The hooks below stand for the engine: they answer `$.session.*` and `$.process.run`.
 */
import { expect, test } from 'claude-code/testing'

type Run = { argv: readonly string[]; stdin?: string }

/** Fake session id/cwd and a process runner; returns the calls the mod made. */
function engine(on: any, run: (argv: readonly string[]) => { exitCode: number; stdout: string; stderr: string }) {
  const calls: Run[] = []
  on('session.id', () => ({ value: 'aaaaaaaa-1111-2222-3333-444444444444' }))
  on('session.cwd', () => ({ value: '/Users/me/app' }))
  on('process.run', (_: unknown, e: any) => {
    calls.push({ argv: e.argv, stdin: e.init?.stdin ?? e.stdin })
    return { value: run(e.argv) }
  })
  return calls
}

const ok = (stdout: string) => ({ exitCode: 0, stdout, stderr: '' })

test('save runs archive.py with the session and shows its report', async ($, on) => {
  const calls = engine(on, () => ok('saved 3 new messages (10 total) to /a/app/x\n'))

  const out = await $.command.run({ command: 'session-archive:save', args: '' })

  expect(out.text).toBe('saved 3 new messages (10 total) to /a/app/x')
  expect(calls.length).toBe(1)
  const argv = calls[0]!.argv
  expect(argv[0]).toBe('python3')
  expect(String(argv[1])).toMatch(/scripts\/archive\.py$/)
  expect(argv[argv.length - 1]).toBe('command')
  const payload = JSON.parse(String(calls[0]!.stdin))
  expect(payload.command_name).toBe('session-archive:save')
  expect(payload.command_args).toBe('')
  expect(payload.session_id).toBe('aaaaaaaa-1111-2222-3333-444444444444')
  expect(payload.cwd).toBe('/Users/me/app')
})

test('arguments are passed through trimmed, for save all and import', async ($, on) => {
  const calls = engine(on, () => ok('done'))

  await $.command.run({ command: 'session-archive:save', args: '  all ' })
  await $.command.run({ command: 'session-archive:import', args: '--list' })

  expect(JSON.parse(String(calls[0]!.stdin)).command_args).toBe('all')
  const second = JSON.parse(String(calls[1]!.stdin))
  expect(second.command_name).toBe('session-archive:import')
  expect(second.command_args).toBe('--list')
})

test('the configured archive folder is passed to archive.py', { options: { archive_dir: '~/My Archive' } }, async ($, on) => {
  const calls = engine(on, () => ok('done'))

  await $.command.run({ command: 'session-archive:save', args: '' })

  const argv = calls[0]!.argv
  const i = argv.indexOf('--archive-dir')
  expect(i).toBeGreaterThan(0)
  expect(argv[i + 1]).toBe('~/My Archive')
})

test('falls back to python when python3 is missing', async ($, on) => {
  const calls = engine(on, argv => {
    if (argv[0] === 'python3') throw new Error('spawn python3 ENOENT')
    return ok('saved 1 new messages')
  })

  const out = await $.command.run({ command: 'session-archive:save', args: '' })

  expect(calls.map(c => c.argv[0])).toEqual(['python3', 'python'])
  expect(out.text).toBe('saved 1 new messages')
})

test('without any Python the command still answers, telling what to install', async ($, on) => {
  engine(on, () => { throw new Error('ENOENT') })

  const out = await $.command.run({ command: 'session-archive:save', args: '' })

  expect(out.text).toMatch(/needs Python 3 on PATH/)
})

test('a failing script shows its error instead of an empty answer', async ($, on) => {
  engine(on, () => ({ exitCode: 1, stdout: '', stderr: 'Traceback: boom' }))

  const out = await $.command.run({ command: 'session-archive:import', args: '' })

  expect(out.text).toMatch(/boom/)
})
