import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import Portfolio from "./pages/Portfolio";
import Dashboard from "./pages/Dashboard";

// Heavy screens (three.js, web-ifc) are code-split.
const Viewer = lazy(() => import("./pages/Viewer"));
const Model = lazy(() => import("./pages/Model"));
const Verification = lazy(() => import("./pages/Verification"));
const Elements = lazy(() => import("./pages/Elements"));
const Schedule = lazy(() => import("./pages/Schedule"));
const QC = lazy(() => import("./pages/QC"));
const Uploads = lazy(() => import("./pages/Uploads"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<Loading label="Loading screen" />}>
        <Routes>
          <Route path="/" element={<Portfolio />} />
          <Route path="/p/:pid" element={<Dashboard />} />
          <Route path="/p/:pid/model" element={<Model />} />
          <Route path="/p/:pid/viewer" element={<Viewer />} />
          <Route path="/p/:pid/verify" element={<Verification />} />
          <Route path="/p/:pid/elements" element={<Elements />} />
          <Route path="/p/:pid/schedule" element={<Schedule />} />
          <Route path="/p/:pid/qc" element={<QC />} />
          <Route path="/p/:pid/uploads" element={<Uploads />} />
          <Route path="/p/:pid/settings" element={<Settings />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
