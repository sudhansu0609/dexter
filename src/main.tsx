import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import Popup from './Popup';
import { ErrorBoundary } from './components/ErrorBoundary';
import { PairingGate } from './components/PairingScreen';
import './index.css';

// One bundle, two faces: `/?mode=popup` is the small always-there window with
// Dexter's latest message; anything else is the full app (v8).
const mode = new URLSearchParams(window.location.search).get('mode');
if (mode === 'popup') document.body.classList.add('is-popup');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      {mode === 'popup' ? (
        <Popup />
      ) : (
        <PairingGate>
          <App />
        </PairingGate>
      )}
    </ErrorBoundary>
  </React.StrictMode>
);

// Install the PWA service worker so the phone can add Dexter to its home screen
// and open it full-screen. Only over http(s) (never Electron's file://), and
// never for the popup shell. Failure here must not break the app.
if (
  mode !== 'popup' &&
  'serviceWorker' in navigator &&
  /^https?:$/.test(window.location.protocol)
) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {
      /* SW is a progressive enhancement - fine to skip */
    });
  });
}
