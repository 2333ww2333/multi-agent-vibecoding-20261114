import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { Providers } from './app/providers';
import { applyInitialTheme } from './shared/stores/ui';
import './shared/styles/tokens.css';
import './shared/styles/global.css';
import './app/layout/shell.css';

applyInitialTheme();

const container = document.getElementById('root');
if (!container) throw new Error('#root not found');

createRoot(container).render(
  <StrictMode>
    <Providers>
      <App />
    </Providers>
  </StrictMode>,
);
