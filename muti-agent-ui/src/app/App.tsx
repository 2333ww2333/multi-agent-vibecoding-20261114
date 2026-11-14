import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';
import { AppShell } from './layout/AppShell';
import { ChatPage } from '../features/chat/ChatPage';
import { SettingsPage } from '../features/settings/SettingsPage';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <ChatPage /> },
      { path: 'sessions/:sessionId', element: <ChatPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
