import { Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router';

import { AppHeader } from './components/AppHeader';
import { useT } from './lib/locale';
import { ProgressScreen } from './screens/ProgressScreen';
import { ExportScreen, ReviewScreen } from './screens/Stubs';
import { UploadScreen } from './screens/UploadScreen';

// Code-based routing: no routeTree.gen.ts, no file-based plugin.

function RootLayout() {
  const { t } = useT();
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-30 focus:rounded-[12px] focus:bg-surface focus:px-3 focus:py-2"
      >
        {t('app.skip')}
      </a>
      <AppHeader />
      <main id="main">
        <Outlet />
      </main>
    </>
  );
}

const rootRoute = createRootRoute({ component: RootLayout });

const uploadRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: UploadScreen,
});

const progressRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/jobs/$jobId',
  component: function ProgressRoute() {
    const { jobId } = progressRoute.useParams();
    return <ProgressScreen key={jobId} jobId={jobId} />;
  },
});

const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review',
  component: ReviewScreen,
});

const exportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/export',
  component: ExportScreen,
});

const routeTree = rootRoute.addChildren([uploadRoute, progressRoute, reviewRoute, exportRoute]);

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  scrollRestoration: true,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
