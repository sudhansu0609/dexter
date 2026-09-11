import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import Popup from './Popup';
import { ErrorBoundary } from './components/ErrorBoundary';
import './index.css';

// One bundle, two faces: `/?mode=popup` is the small always-there window with
// Dexter's latest message; anything else is the full app (v8).
const mode = new URLSearchParams(window.location.search).get('mode');
if (mode === 'popup') document.body.classList.add('is-popup');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>{mode === 'popup' ? <Popup /> : <App />}</ErrorBoundary>
  </React.StrictMode>
);
