import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SchedulerPanel } from '../components/SchedulerPanel';
import type { ScheduleItem } from '../types';

const items: ScheduleItem[] = [
  { id: 'r-1', kind: 'reminder', actionType: 'reminder', label: 'stretch', whenText: 'in 10 minutes' },
  { id: 'a-1', kind: 'action', actionType: 'app', label: 'open Notepad', whenText: 'every weekday at 9am', cron: {} },
];

describe('SchedulerPanel', () => {
  it('lists reminders and actions with their times', () => {
    render(<SchedulerPanel items={items} loading={false} onAdd={vi.fn()} onRemove={vi.fn()} onRunNow={vi.fn()} />);
    expect(screen.getByText('stretch')).toBeInTheDocument();
    expect(screen.getByText('open Notepad')).toBeInTheDocument();
    expect(screen.getByText('in 10 minutes')).toBeInTheDocument();
    expect(screen.getByText(/2 scheduled/)).toBeInTheDocument();
  });

  it('builds a typed action and passes the when text to onAdd', async () => {
    const onAdd = vi.fn(async () => ({ ok: true }));
    render(<SchedulerPanel items={[]} loading={false} onAdd={onAdd} onRemove={vi.fn()} onRunNow={vi.fn()} />);

    // Switch to an "Open app" action, fill both fields, submit.
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'app' } });
    fireEvent.change(screen.getByPlaceholderText(/App name/i), { target: { value: 'Notepad' } });
    fireEvent.change(screen.getByPlaceholderText(/^When/i), { target: { value: 'in 5 minutes' } });
    fireEvent.click(screen.getByRole('button', { name: /Schedule it/i }));

    expect(onAdd).toHaveBeenCalledWith({ type: 'app', app: 'Notepad' }, 'in 5 minutes');
  });

  it('refuses to submit until both what and when are filled', () => {
    const onAdd = vi.fn();
    render(<SchedulerPanel items={[]} loading={false} onAdd={onAdd} onRemove={vi.fn()} onRunNow={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Schedule it/i }));
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByText(/Fill in what and when/i)).toBeInTheDocument();
  });

  it('offers Run now only for actions, and Cancel for everything', () => {
    const onRunNow = vi.fn();
    const onRemove = vi.fn();
    render(<SchedulerPanel items={items} loading={false} onAdd={vi.fn()} onRemove={onRemove} onRunNow={onRunNow} />);
    fireEvent.click(screen.getByTitle('Run now'));
    expect(onRunNow).toHaveBeenCalledWith('a-1');
    expect(screen.getAllByTitle('Cancel')).toHaveLength(2);
  });
});
