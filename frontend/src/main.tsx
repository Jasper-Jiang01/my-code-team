import React from 'react';
import ReactDOM from 'react-dom/client';
import { ChatPage } from './pages/Chat/ChatPage';
import './styles.scss';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ChatPage />
  </React.StrictMode>,
);
