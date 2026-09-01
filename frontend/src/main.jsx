import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import Shell from './Shell';
import { AuthProvider } from './auth';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* The provider sits outside the shell so the sign-in screen, the
        onboarding wizard and the app itself all read one answer to "who is
        this?" - fetched once, on load. */}
    <AuthProvider>
      <Shell>{(props) => <App {...props} />}</Shell>
    </AuthProvider>
  </React.StrictMode>,
);
