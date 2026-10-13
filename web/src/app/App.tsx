import { type ReactNode, Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";

import { Spinner } from "@/ui/primitives";

import { CanteenProvider, homeFor, useAuth } from "./auth";
import { StaffShell } from "./Shell";

const Login = lazy(() => import("@/pages/Login"));
const Overview = lazy(() => import("@/pages/Overview"));
const Plan = lazy(() => import("@/pages/Plan"));
const Kitchen = lazy(() => import("@/pages/Kitchen"));
const Live = lazy(() => import("@/pages/Live"));
const Rescue = lazy(() => import("@/pages/Rescue"));
const RescueNew = lazy(() => import("@/pages/RescueNew"));
const IncidentPage = lazy(() => import("@/pages/Incident"));
const Places = lazy(() => import("@/pages/Places"));
const Menu = lazy(() => import("@/pages/Menu"));
const Feedback = lazy(() => import("@/pages/Feedback"));
const Models = lazy(() => import("@/pages/Models"));
const ModelVersion = lazy(() => import("@/pages/ModelVersion"));
const Dataset = lazy(() => import("@/pages/Dataset"));
const Impact = lazy(() => import("@/pages/Impact"));
const WhatIf = lazy(() => import("@/pages/WhatIf"));
const Data = lazy(() => import("@/pages/Data"));
const SettingsPage = lazy(() => import("@/pages/Settings"));
const Admin = lazy(() => import("@/pages/admin/Admin"));
const StudentSession = lazy(() => import("@/pages/public/StudentSession"));

function Loading() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <Spinner />
    </div>
  );
}

function RequireAuth({ children, roles }: { children: ReactNode; roles?: string[] }) {
  const { status, me } = useAuth();
  const loc = useLocation();
  if (status === "loading") return <Loading />;
  if (status === "signed_out")
    return <Navigate to={loc.pathname.startsWith("/admin") ? "/admin/login" : "/login"} replace state={{ from: loc.pathname + loc.search }} />;
  if (roles && me && !roles.includes(me.user.role)) return <Navigate to={homeFor(me.user.role)} replace />;
  return <>{children}</>;
}


export function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/login" element={<Login portal="organization" />} />
          <Route path="/admin/login" element={<Login portal="admin" />} />
          <Route path="/s/:token" element={<StudentSession />} />
          <Route
            path="/admin/*"
            element={
              <RequireAuth roles={["admin"]}>
                <Admin />
              </RequireAuth>
            }
          />
          <Route
            element={
              <RequireAuth roles={["organization"]}>
                <CanteenProvider>
                  <StaffShell />
                </CanteenProvider>
              </RequireAuth>
            }
          >
            <Route index element={<Overview />} />
            <Route path="plan" element={<Plan />} />
            <Route path="menu" element={<Menu />} />
            <Route path="kitchen" element={<Kitchen />} />
            <Route path="live" element={<Live />} />
            <Route path="rescue" element={<Rescue />} />
            <Route path="rescue/new" element={<RescueNew />} />
            <Route path="rescue/:id" element={<IncidentPage />} />
            <Route path="places" element={<Places />} />
            <Route path="feedback" element={<Feedback />} />
            <Route path="models" element={<Models />} />
            <Route path="models/:id" element={<ModelVersion />} />
            <Route path="dataset" element={<Dataset />} />
            <Route path="impact" element={<Impact />} />
            <Route path="whatif" element={<WhatIf />} />
            <Route path="data" element={<Data />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
