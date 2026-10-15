import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { HandMousePanel, liveLine } from '../components/HandMousePanel';
import type { HandMouseStatus } from '../types';

// The hand mouse on the dashboard (v13.6). Asked for as "where do I see the hand
// tracking on the dashboard" — the answer had been "nowhere", only a floating
// OpenCV window and a tool you had to name.
//
// What is worth a test here is the part that is only true in the UI: a gesture
// mouse is the one feature you cannot debug by looking at what it controls, so the
// panel has to distinguish "the camera cannot see you" from "that shape is bound to
// do nothing", and it has to let you change a binding while it runs.

const POSES = [
  { pose: 'open-palm', label: 'Open palm', hint: 'all four fingers out', action: 'point', default: 'point' },
  { pose: 'index', label: 'Index finger', hint: 'index out, the rest curled', action: 'point', default: 'point' },
  { pose: 'two-fingers', label: 'Two fingers', hint: 'index and middle out together', action: 'scroll', default: 'scroll' },
  { pose: 'fist', label: 'Fist', hint: 'nothing out', action: 'park', default: 'park' },
  { pose: 'pinch-index', label: 'Pinch', hint: 'thumb touching the index fingertip', action: 'pinch', default: 'pinch' },
];

const ACTIONS = [
  { action: 'point', label: 'Move the pointer' },
  { action: 'pinch', label: 'Left button, held (click and drag)' },
  { action: 'right-pinch', label: 'Right button, held' },
  { action: 'scroll', label: "Scroll with the hand's movement" },
  { action: 'park', label: 'Nothing — release every button' },
];

function status(extra: Partial<HandMouseStatus> = {}): HandMouseStatus {
  return {
    running: true,
    pose: 'open-palm',
    action: 'point',
    gesture: 'point',
    bindings: Object.fromEntries(POSES.map((p) => [p.pose, p.action])),
    poses: POSES,
    actions: ACTIONS,
    buttons: { left: false, right: false, middle: false },
    locked: false,
    dragging: false,
    speed: 1.0,
    region: [0.18, 0.14, 0.82, 0.78],
    dragThresholdPx: 120,
    confirmFrames: 2,
    fps: 19.4,
    camera: 0,
    engine: 'onnx',
    dryRun: false,
    frames: 400,
    handFrames: 200,
    seeingHand: true,
    minutes: 2.5,
    hotkey: 'ctrl+alt+h',
    ready: true,
    why: '',
    error: '',
    palmScore: 0.87,
    bestScore: 0.9,
    palmNeeded: 0.5,
    preview: { enabled: true, open: true, error: '' },
    ...extra,
  };
}

const noop = () => {};

describe('the live line', () => {
  it('names the shape and what it did', () => {
    expect(liveLine(status())).toBe('Open palm → moving the pointer');
  });

  it('distinguishes a pose bound to nothing from a camera that sees nothing', () => {
    const parked = liveLine(status({ pose: 'fist', action: 'park' }));
    expect(parked).toContain('Fist');
    expect(parked).toContain('on purpose');

    const blind = liveLine(status({ seeingHand: false, palmScore: 0.21 }));
    expect(blind).toContain('0.21');
    expect(blind).toContain('0.50');
  });

  it('says what is missing when it was never set up', () => {
    const line = liveLine(status({ running: false, ready: false, why: "the hand models aren't downloaded yet" }));
    expect(line).toContain("models aren't downloaded");
  });
});

describe('the panel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the live pose, the frame rate and how much of the time a hand was visible', () => {
    render(<HandMousePanel status={status()} busy={false} onStart={noop} onStop={noop} onBind={noop} />);
    expect(screen.getByText('Open palm → moving the pointer')).toBeTruthy();
    expect(screen.getByText(/19 fps/)).toBeTruthy();
    expect(screen.getByText(/50% of frames had a hand/)).toBeTruthy();
  });

  it('offers a rehearsal as well as a start, so a changed gesture can be tried safely', () => {
    const started: boolean[] = [];
    render(
      <HandMousePanel
        status={status({ running: false })}
        busy={false}
        onStart={(dry) => started.push(!!dry)}
        onStop={noop}
        onBind={noop}
      />,
    );
    fireEvent.click(screen.getByText('Rehearse'));
    fireEvent.click(screen.getByText('Start'));
    expect(started).toEqual([true, false]);
  });

  it('will not offer to start when the models are not there', () => {
    render(
      <HandMousePanel
        status={status({ running: false, ready: false, why: 'no models' })}
        busy={false}
        onStart={noop}
        onStop={noop}
        onBind={noop}
      />,
    );
    expect((screen.getByText('Start').closest('button') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/set up hand tracking/)).toBeTruthy();
  });

  it('rebinds a pose from the editor', async () => {
    const bound: { pose: string; action: string }[] = [];
    render(
      <HandMousePanel
        status={status()}
        busy={false}
        onStart={noop}
        onStop={noop}
        onBind={(pose, action) => bound.push({ pose, action })}
      />,
    );
    fireEvent.click(screen.getByText('Gestures'));
    const select = await screen.findByLabelText('What Fist does');
    fireEvent.change(select, { target: { value: 'right-pinch' } });
    expect(bound).toEqual([{ pose: 'fist', action: 'right-pinch' }]);
  });

  it('marks a gesture that is no longer the default', async () => {
    const changed = status({
      poses: POSES.map((p) => (p.pose === 'fist' ? { ...p, action: 'right-pinch' } : p)),
    });
    render(<HandMousePanel status={changed} busy={false} onStart={noop} onStop={noop} onBind={noop} />);
    fireEvent.click(screen.getByText('Gestures'));
    await waitFor(() => expect(screen.getByText(/changed/)).toBeTruthy());
  });

  it('says out loud when it is only rehearsing', () => {
    render(<HandMousePanel status={status({ dryRun: true })} busy={false} onStart={noop} onStop={noop} onBind={noop} />);
    expect(screen.getByText(/the real pointer is not being moved/)).toBeTruthy();
  });
});

// ───────────── speed, and the click that has to land (v13.8) ─────────────
//
// "Can we control the speed of the hand mouse pointer? Also clicking seems difficult
// as it is unstable." The slider is the answer to the first; for the second, the
// pointer is now held still through a click - and the panel has to *say* so, because
// a pointer that stops looks like a freeze, which is a word this project has earned.

describe('speed and click feedback', () => {
  it('shows the current speed and reports a new one when the slider is let go', () => {
    const asked: number[] = [];
    render(
      <HandMousePanel
        status={status({ speed: 1.2 })}
        busy={false}
        onStart={noop}
        onStop={noop}
        onBind={noop}
        onSpeed={(n) => asked.push(n)}
      />,
    );
    const slider = screen.getByLabelText('Pointer speed') as HTMLInputElement;
    expect(slider.value).toBe('1.2');
    expect(screen.getByText('1.2×')).toBeTruthy();

    fireEvent.change(slider, { target: { value: '2' } });
    expect(screen.getByText('2.0×')).toBeTruthy();
    expect(asked).toEqual([]);            // not on every pixel of the drag
    fireEvent.pointerUp(slider);
    expect(asked).toEqual([2]);           // once, when you let go
  });

  it('says when the pointer is holding still, rather than just stopping', () => {
    expect(liveLine(status({ pose: 'pinch-index', action: 'pinch', locked: true })))
      .toContain('holding still');
  });

  it('and says when it has become a drag', () => {
    expect(liveLine(status({ pose: 'pinch-index', action: 'pinch', dragging: true })))
      .toContain('dragging');
  });

  it('has no slider when nothing can be done with it', () => {
    render(<HandMousePanel status={status()} busy={false} onStart={noop} onStop={noop} onBind={noop} />);
    expect(screen.queryByLabelText('Pointer speed')).toBeNull();
  });
});
