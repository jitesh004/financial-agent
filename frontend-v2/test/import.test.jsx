/* The import: the wizard, the uploader, and the state machine behind them.
 *
 * This is the only flow in the app where the browser is not the source of
 * truth - the work is a server-side job and the stage is derived from it. So
 * the tests that matter are about that derivation: where the wizard lands for
 * a given server state, and what it does NOT claim. "Done" in particular has
 * exactly one meaning here, and a parse that fills the staging area is not it.
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReviewStep } from '../src/screens/import/review';
import Uploader from '../src/screens/import/Uploader';
import ImportWizard from '../src/screens/import/ImportWizard';
import useImport, { stageFor, suggestedCap, rowKey } from '../src/screens/import/useImport';
import { createServer, renderScreen, fixture } from './harness';

let server;
beforeEach(() => { server = createServer(); });

/* ── the state machine ───────────────────────────────────────────────────── */

describe('which stage a job means', () => {
  it('is idle when there is no job', () => {
    expect(stageFor(null)).toBe('idle');
  });

  it('separates running from finished, per kind', () => {
    expect(stageFor({ kind: 'scan', active: true })).toBe('scanning');
    expect(stageFor({ kind: 'scan', active: false })).toBe('select');
    expect(stageFor({ kind: 'download', active: true })).toBe('downloading');
    expect(stageFor({ kind: 'stage_parse', active: true })).toBe('parsing');
    expect(stageFor({ kind: 'stage_process', active: true })).toBe('processing');
  });

  it('never calls a finished parse "done" — nothing has reached the ledger yet', () => {
    // Reading fills the staging area and stops there. Saying "done" at this
    // point is the one claim this whole flow exists to stop making.
    expect(stageFor({ kind: 'stage_parse', active: false })).toBe('staged');
    expect(stageFor({ kind: 'process', active: false })).toBe('staged');
    expect(stageFor({ kind: 'alerts', active: false })).toBe('staged');
    // Only the step that actually changes what a screen shows is "done".
    expect(stageFor({ kind: 'stage_process', active: false })).toBe('done');
  });

  it('does not present a cancelled run as a completed import', () => {
    expect(stageFor({ kind: 'stage_process', active: false, status: 'cancelled' }))
      .not.toBe('done');
    expect(stageFor({ kind: 'stage_parse', active: false, status: 'failed' }))
      .not.toBe('staged');
  });

  it('reports an interrupted job as interrupted, whatever it was doing', () => {
    expect(stageFor({ kind: 'download', active: false, status: 'interrupted' }))
      .toBe('interrupted');
  });
});

describe('how far back a scan reaches', () => {
  it('raises the message cap with the window, so a long look-back is not capped short',
    () => {
      const windows = [3, 12, 36, 60, 120];
      const caps = windows.map(suggestedCap);
      for (let i = 1; i < caps.length; i += 1) {
        expect(caps[i]).toBeGreaterThanOrEqual(caps[i - 1]);
      }
      expect(suggestedCap(null)).toBeGreaterThanOrEqual(caps[caps.length - 1]);
    });
});

describe('identifying one attachment', () => {
  it('keys a row by message, name and size together', () => {
    const a = { message_id: 'm1', filename: 'stmt.pdf', size: 10 };
    const b = { message_id: 'm1', filename: 'stmt.pdf', size: 11 };
    expect(rowKey(a)).not.toBe(rowKey(b));
    expect(rowKey(a)).toBe(rowKey({ ...a }));
  });
});

/* ── the uploader ────────────────────────────────────────────────────────── */

function file(name, type = 'application/pdf') {
  return new File(['hello'], name, { type });
}

describe('Uploader', () => {
  it('queues files and sends them all in one request', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'POST /api/upload': { job_id: 'up-1', staged: 2, rejected: [] },
        'GET /api/jobs/up-1': {
          id: 'up-1', kind: 'process', active: false, status: 'complete',
          progress: 1, result: { staged: 2 },
        },
      },
    });
    const done = [];
    renderScreen(<Uploader onComplete={(r) => done.push(r)} />, { route: '/' });

    await screen.findByText(/drop your statements here/i, {}, { timeout: 8000 });
    const input = document.querySelector('input[type=file]');
    await user.upload(input, [file('bank.pdf'), file('card.pdf')]);

    expect(await screen.findByText('bank.pdf', {}, { timeout: 8000 })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /add 2 files/i }));

    await waitFor(() => {
      expect(server.lastCall('/api/upload', 'POST')).toBeTruthy();
    }, { timeout: 8000 });
    await waitFor(() => expect(done.length).toBe(1), { timeout: 8000 });
  });

  it('de-dupes a batch dropped twice', async () => {
    const user = userEvent.setup();
    renderScreen(<Uploader onComplete={() => {}} />, { route: '/' });

    await screen.findByText(/drop your statements here/i, {}, { timeout: 8000 });
    const input = document.querySelector('input[type=file]');
    await user.upload(input, [file('bank.pdf')]);
    await user.upload(input, [file('bank.pdf')]);

    await waitFor(() => {
      expect(screen.getAllByText('bank.pdf').length).toBe(1);
    }, { timeout: 8000 });
    expect(screen.getByRole('button', { name: /add 1 file$/i })).toBeTruthy();
  });

  it('drops one queued file without losing the rest', async () => {
    const user = userEvent.setup();
    renderScreen(<Uploader onComplete={() => {}} />, { route: '/' });

    await screen.findByText(/drop your statements here/i, {}, { timeout: 8000 });
    const input = document.querySelector('input[type=file]');
    await user.upload(input, [file('bank.pdf'), file('card.pdf')]);
    await user.click(await screen.findByLabelText('Remove bank.pdf', {}, { timeout: 8000 }));

    await waitFor(() => {
      expect(screen.queryByText('bank.pdf')).toBeNull();
      expect(screen.getByText('card.pdf')).toBeTruthy();
    }, { timeout: 8000 });
  });

  it('says what the server refused, rather than dropping it silently', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'POST /api/upload': {
          job_id: null, staged: 0, rejected: ['notes.txt is not a statement'],
        },
      },
    });
    renderScreen(<Uploader onComplete={() => {}} />, { route: '/' });

    await screen.findByText(/drop your statements here/i, {}, { timeout: 8000 });
    const input = document.querySelector('input[type=file]');
    await user.upload(input, [file('notes.txt', 'text/plain')]);
    await user.click(await screen.findByRole('button', { name: /add 1 file/i }, {
      timeout: 8000,
    }));

    expect(await screen.findByText(
      /notes\.txt is not a statement/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('reports a failed read instead of returning to an empty dropzone', async () => {
    const user = userEvent.setup();
    server = createServer({
      routes: {
        'POST /api/upload': { job_id: 'up-2', staged: 1, rejected: [] },
        'GET /api/jobs/up-2': {
          id: 'up-2', kind: 'process', active: false, status: 'failed',
          progress: 1, errors: ['the PDF is password protected'],
        },
      },
    });
    renderScreen(<Uploader onComplete={() => {}} />, { route: '/' });

    await screen.findByText(/drop your statements here/i, {}, { timeout: 8000 });
    const input = document.querySelector('input[type=file]');
    await user.upload(input, [file('locked.pdf')]);
    await user.click(await screen.findByRole('button', { name: /add 1 file/i }, {
      timeout: 8000,
    }));

    expect(await screen.findByText(
      /password protected/i, {}, { timeout: 8000 })).toBeTruthy();
  });
});

/* ── the wizard ──────────────────────────────────────────────────────────── */

/* The wizard takes the import state as a prop, so a test can put the import in
   any server state without having to drive a job to it. */
function Harness({ open = true, onClose = () => {}, onImported = () => {} }) {
  const mailbox = useImport({ open, onImported });
  return <ImportWizard mailbox={mailbox} open={open} onClose={onClose} onImported={onImported} />;
}

describe('ImportWizard', () => {
  it('offers files from this computer even with no mailbox configured', async () => {
    server = createServer({
      routes: {
        'GET /api/gmail/status': {
          ...fixture('/api/gmail/status'), available: false, connected: false,
        },
      },
    });
    renderScreen(<Harness />, { route: '/' });

    expect(await screen.findByRole('dialog', {}, { timeout: 8000 })).toBeTruthy();
    // The upload path is not behind the mailbox: somebody with statements on
    // disk and no Gmail must still be able to get them in.
    expect(await screen.findByText(
      /drop your statements here/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('keeps every step reachable with no mailbox connected', async () => {
    server = createServer({
      routes: {
        'GET /api/gmail/status': {
          ...fixture('/api/gmail/status'), available: true, connected: false,
        },
      },
    });
    const user = userEvent.setup();
    renderScreen(<Harness />, { route: '/' });

    const dialog = await screen.findByRole('dialog', {}, { timeout: 8000 });
    // The offer to connect is there…
    expect(within(dialog).getByRole('button', { name: /connect gmail/i })).toBeTruthy();
    // …and so is the alternative it points at, on the same screen.
    expect(await screen.findByText(
      /drop your statements here/i, {}, { timeout: 8000 })).toBeTruthy();
    // …and the rest of the import is still navigable.
    await user.click(within(dialog).getByRole('button', { name: /^next$/i }));
    expect(within(dialog).getByRole('button', { name: /^next$/i })).toBeTruthy();
  });

  it('shows every step of the import, in order', async () => {
    renderScreen(<Harness />, { route: '/' });
    const dialog = await screen.findByRole('dialog', {}, { timeout: 8000 });
    for (const label of ['Source', 'Scan', 'Choose', 'Read', 'Review', 'Build']) {
      expect(within(dialog).getAllByRole('button', { name: new RegExp(label) }).length,
        label).toBeGreaterThan(0);
    }
  });

  it('closing it does not cancel anything', async () => {
    const user = userEvent.setup();
    const { rerender } = renderScreen(<Harness open />, { route: '/' });
    await screen.findByRole('dialog', {}, { timeout: 8000 });

    const before = server.calls.filter((c) => /\/cancel$/.test(c.path)).length;
    await user.click(within(screen.getByRole('dialog')).getAllByRole(
      'button', { name: /close/i })[0]);

    await new Promise((r) => { setTimeout(r, 300); });
    expect(server.calls.filter((c) => /\/cancel$/.test(c.path)).length).toBe(before);
  });
});


/* ── what the review step says about each document ───────────────────────── */

/** One staging group, in the shape /api/staging/review answers. */
function stagingWith(file) {
  return {
    groups: [{
      key: 'g1',
      account_label: 'CDSL/NSDL portfolio',
      account_type: 'investment',
      kind: 'portfolio',
      kind_label: 'Investments',
      kind_note: 'Holdings on one date, not a ledger of transactions.',
      files: [{
        id: 'f1',
        filename: 'holdings.pdf',
        origin: 'upload',
        selected: true,
        superseded_by: null,
        parse_status: 'ok',
        parse_message: '',
        period_start: null,
        period_end: null,
        row_count: 0,
        debits: '0',
        credits: '0',
        recon_status: 'not_applicable',
        warnings: [],
        ...file,
      }],
      file_count: 1,
      selected_count: 1,
      superseded_count: 0,
      failed_count: 0,
      unbalanced_count: 0,
      row_count: file.row_count ?? 0,
      debits: '0',
      credits: '0',
      first: null,
      last: null,
      included: true,
      partial: false,
    }],
    total: 1, selected: 1, parsed: 1, pending: 0, superseded: 0,
    rows: file.row_count ?? 0, items: 0, processed: 0,
  };
}

describe('the review step', () => {
  it('says when a document was read and yielded nothing', async () => {
    createServer({
      routes: {
        'GET /api/staging/review': stagingWith({
          parse_message: '0 holding(s). No holdings were read.',
        }),
      },
    });
    renderScreen(<ReviewStep onChanged={() => {}} />, { route: '/' });

    // The parse did not fail and the reconciliation did not either, so this
    // used to show a filename and nothing else - ticked, included in the
    // build, and contributing no rows, with no explanation anywhere.
    expect(await screen.findByText(
      /no holdings were read/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('falls back to plain words when the reader gave no message', async () => {
    createServer({
      routes: { 'GET /api/staging/review': stagingWith({ parse_message: '' }) },
    });
    renderScreen(<ReviewStep onChanged={() => {}} />, { route: '/' });
    expect(await screen.findByText(
      /nothing was found in it to count/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('shows the limits the reader reported, even on a document that read fine', async () => {
    createServer({
      routes: {
        'GET /api/staging/review': stagingWith({
          row_count: 12,
          warnings: ['No valuation date found; the holdings cannot be dated.'],
        }),
      },
    });
    renderScreen(<ReviewStep onChanged={() => {}} />, { route: '/' });
    expect(await screen.findByText(
      /no valuation date found/i, {}, { timeout: 8000 })).toBeTruthy();
  });

  it('does not repeat a warning that is already the note', async () => {
    createServer({
      routes: {
        'GET /api/staging/review': stagingWith({
          parse_message: 'Read, but nothing was found in it to count.',
          warnings: ['Read, but nothing was found in it to count.'],
        }),
      },
    });
    renderScreen(<ReviewStep onChanged={() => {}} />, { route: '/' });
    await screen.findByText(/nothing was found in it to count/i, {}, { timeout: 8000 });
    expect(screen.getAllByText(/nothing was found in it to count/i).length).toBe(1);
  });
});
