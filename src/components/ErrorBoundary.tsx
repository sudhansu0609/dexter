import React from 'react';

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

// Stops a render-time crash in any panel from blanking the whole window
// (roadmap Phase 6, item 10). A reload button is the escape hatch since the app
// is a single long-lived desktop surface.
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Dexter UI crashed', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: '24px', maxWidth: '460px', margin: '40px auto' }}>
          <div className="dexter-panel dexter-glow-border" style={{ padding: '20px', textAlign: 'center' }}>
            <div style={{ fontSize: '32px', marginBottom: '8px' }}>🧠💥</div>
            <h2 style={{ fontSize: '15px', color: 'var(--text)', marginBottom: '8px' }}>
              Dexter hit a snag
            </h2>
            <div style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '16px', wordBreak: 'break-word' }}>
              {this.state.error.message || 'An unexpected error occurred in the interface.'}
            </div>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                background: 'var(--accent)',
                color: '#ffffff',
                padding: '8px 18px',
                borderRadius: '10px',
                fontWeight: 800,
                fontSize: '12px',
              }}
            >
              Reload Dexter
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
