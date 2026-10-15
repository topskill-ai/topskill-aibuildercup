import { Navigate, useNavigate, useSearchParams } from "react-router";
import { LoginForm } from "@/components/LoginDialog";
import { PageLoading } from "@/components/layout/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";
import { useAuth } from "@/lib/auth";
import { safeNextPath } from "@/lib/nav";

/** Full-page sign-in (SPEC §11.2). Honours `?next=` (same-origin paths only). */
export default function LoginPage() {
  const { status } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const next = safeNextPath(params.get("next"));
  const mode = params.get("mode") === "sign_up" ? "sign_up" : "sign_in";

  if (status === "loading") return <PageLoading />;
  if (status === "signed_in") return <Navigate to={next} replace />;

  return (
    <div className="page-container flex flex-1 items-start justify-center py-10 sm:py-16">
      <Card className="w-full max-w-md">
        <CardBody className="p-6 sm:p-8">
          <h1 className="text-2xl font-semibold tracking-display">
            {mode === "sign_up" ? "Create your account" : "Welcome back"}
          </h1>
          <p className="mt-1 mb-6 text-sm text-muted">
            Sign in to chat with agents, publish your own and manage your credits.
          </p>
          <LoginForm initialMode={mode} onSuccess={() => navigate(next, { replace: true })} />
        </CardBody>
      </Card>
    </div>
  );
}
