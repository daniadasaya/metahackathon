import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './index.css';
import { DeviceStage } from './components/DeviceStage';
import { router } from './routes';

const container = document.getElementById('root');

if (container === null) {
  throw new Error('Root element "#root" is missing from index.html');
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

createRoot(container).render(
  <StrictMode>
    <DeviceStage>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </DeviceStage>
  </StrictMode>,
);
