import { Outlet, createRootRoute, createRoute, createRouter, useRouterState } from '@tanstack/react-router';
import { useEffect } from 'react';

import { AppHeader } from './components/AppHeader';
import { useT } from './lib/locale';
import { ExportScreen } from './screens/ExportScreen';
import { PreviewScreen } from './screens/PreviewScreen';
import { ProgressScreen } from './screens/ProgressScreen';
import { ReviewScreen } from './screens/ReviewScreen';
import { UploadScreen } from './screens/UploadScreen';

// Code-based routing: no routeTree.gen.ts, no file-based plugin.

function RootLayout() {
  const { t } = useT();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  // Router scroll restoration is off (it writes sessionStorage; only the six `kk:*` keys may be
  // persisted), so each screen starts at the top.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-30 focus:rounded-[12px] focus:bg-surface focus:px-3 focus:py-2"
      >
        {t('app.skip')}
      </a>
      <AppHeader />
      <main id="main" key={pathname} className="kk-screen">
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

const previewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/preview',
  component: PreviewScreen,
});

const exportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/export',
  component: ExportScreen,
});

const routeTree = rootRoute.addChildren([
  uploadRoute,
  progressRoute,
  reviewRoute,
  previewRoute,
  exportRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  scrollRestoration: false,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
