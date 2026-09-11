// The backend does not own port 8098 - it owns whichever port was free when it
// started (GUARDIAN_PLAN.md section 11). The page is served by that backend, so
// the one correct answer is the origin it was loaded from; a literal here would
// be wrong the first time the Studio or BuzzEdit got to 8098 first.

import { describe, expect, it } from 'vitest';

import { API_BASE } from '../api';

describe('API_BASE', () => {
  it('is the origin the page was served from, whatever port that is', () => {
    expect(API_BASE).toBe(window.location.origin);
  });

  it('carries no hardcoded port', () => {
    expect(API_BASE).not.toMatch(/:80\d\d/);
  });
});
