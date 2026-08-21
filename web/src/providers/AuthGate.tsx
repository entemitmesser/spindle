import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type PropsWithChildren, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Gates the whole app behind a password when auth is enabled
 * (`SPINDLE_AUTH_ENABLED`), per phase-09-hardening.md task 1. There's no
 * dedicated `/login` route -- an unauthenticated visitor sees this screen in
 * place of the app at whatever URL they landed on, and it disappears the
 * moment `GET /auth/me` reports `authenticated: true`. `EventProvider`
 * (SSE) is mounted by the caller only inside the authenticated branch, so
 * the event stream never even attempts a connection before login.
 */
export function AuthGate({ children }: PropsWithChildren) {
  const { data, isPending } = useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: api.auth.me,
  });

  if (isPending) return null;
  if (!data || !data.auth_enabled || data.authenticated) return <>{children}</>;

  return <LoginScreen needsSetup={data.needs_setup} />;
}

function LoginScreen({ needsSetup }: { needsSetup: boolean }) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (needsSetup && password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      await (needsSetup ? api.auth.setup(password) : api.auth.login(password));
      await queryClient.invalidateQueries({ queryKey: queryKeys.auth.me() });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bg-background flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{needsSetup ? "Set up Spindle" : "Sign in to Spindle"}</CardTitle>
          <CardDescription>
            {needsSetup
              ? "Choose a password to protect this instance."
              : "Enter your password to continue."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={handleSubmit}>
            <Input
              type="password"
              placeholder="Password"
              autoFocus
              autoComplete={needsSetup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={error ? true : undefined}
            />
            {needsSetup && (
              <Input
                type="password"
                placeholder="Confirm password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                aria-invalid={error ? true : undefined}
              />
            )}
            {error && <p className="text-destructive text-sm">{error}</p>}
            <Button type="submit" disabled={submitting || !password}>
              {needsSetup ? "Set password" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
