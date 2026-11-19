"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { mutate } from "swr";
import { useCurrentUser } from "@/components/app-shell";
import { useToast } from "@/components/toast";
import { Button, Card, ConfirmDialog, ErrorNotice, Field, Notice, PageHeader, Skeleton, inputClass, usePageTitle } from "@/components/ui";
import { deleteRequest, errorMessage, postJson } from "@/lib/api";

export default function AccountPage() {
  usePageTitle("Account");
  const router = useRouter();
  const toast = useToast();
  const { data: user, mutate: refreshUser } = useCurrentUser();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Fill the form once the account has loaded.
    if (user) setName(user.name);
  }, [user]);

  async function leave() {
    await mutate(() => true, undefined, { revalidate: false });
    router.replace("/login");
  }

  async function saveName(event: FormEvent) {
    event.preventDefault();
    setBusy("name");
    try {
      await postJson("/api/auth/me", { name }, "PATCH");
      await refreshUser();
      toast.success("Name saved.");
    } catch (caught) {
      toast.error(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    const form = new FormData(target);
    if (form.get("new_password") !== form.get("repeat")) {
      setPasswordError("The new passwords do not match.");
      return;
    }
    setBusy("password");
    setPasswordError(null);
    try {
      await postJson("/api/auth/change-password", {
        current_password: form.get("current_password"),
        new_password: form.get("new_password"),
      });
      target.reset();
      toast.success("Password changed. Other devices were signed out.");
    } catch (caught) {
      setPasswordError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function signOutEverywhere() {
    setBusy("signout");
    try {
      await postJson("/api/auth/logout-all");
      await leave();
    } catch (caught) {
      toast.error(errorMessage(caught));
      setBusy(null);
      setConfirmSignOut(false);
    }
  }

  async function deleteAccount(event: FormEvent) {
    event.preventDefault();
    setBusy("delete");
    setDeleteError(null);
    try {
      await deleteRequest("/api/auth/me", { password: deletePassword });
      await leave();
    } catch (caught) {
      setDeleteError(errorMessage(caught));
      setBusy(null);
    }
  }

  if (!user) {
    return <div className="mx-auto w-full max-w-2xl space-y-3 p-6"><Skeleton className="h-8 w-40" /><Skeleton className="h-40" /></div>;
  }

  return (
    <main className="mx-auto w-full max-w-2xl flex-1 space-y-4 p-4 sm:p-6">
      <PageHeader title="Account" description={user.email} />

      <Card title="Profile">
        <form onSubmit={saveName} className="flex items-end gap-2">
          <div className="flex-1">
            <Field label="Name">
              <input value={name} required maxLength={200} onChange={(event) => setName(event.target.value)} className={inputClass} />
            </Field>
          </div>
          <Button type="submit" busy={busy === "name"} disabled={name.trim() === user.name || !name.trim()}>Save</Button>
        </form>
      </Card>

      <Card title="Password">
        <form onSubmit={changePassword} className="space-y-3">
          {passwordError && <ErrorNotice error={new Error(passwordError)} />}
          <Field label="Current password">
            <input name="current_password" type="password" required autoComplete="current-password" className={inputClass} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="New password" hint="Use at least 10 characters.">
              <input name="new_password" type="password" required minLength={10} autoComplete="new-password" className={inputClass} />
            </Field>
            <Field label="Repeat new password">
              <input name="repeat" type="password" required minLength={10} autoComplete="new-password" className={inputClass} />
            </Field>
          </div>
          <Button type="submit" variant="primary" busy={busy === "password"}>Change password</Button>
        </form>
      </Card>

      <Card title="Sessions">
        <p className="mb-3 text-sm text-ink-2">Sign out on every device and browser, including this one.</p>
        <Button onClick={() => setConfirmSignOut(true)}>Sign out everywhere</Button>
        {confirmSignOut && (
          <ConfirmDialog title="Sign out everywhere?" confirmLabel="Sign out everywhere" busy={busy === "signout"} onConfirm={signOutEverywhere} onCancel={() => setConfirmSignOut(false)}>
            You will need to sign in again on every device.
          </ConfirmDialog>
        )}
      </Card>

      <Card title="Delete account">
        <Notice tone="warning">This permanently deletes your account and all of your projects, chats, tests and documentation.</Notice>
        <form onSubmit={deleteAccount} className="mt-3 space-y-3">
          {deleteError && <ErrorNotice error={new Error(deleteError)} />}
          <Field label="Enter your password to confirm">
            <input value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} type="password" required autoComplete="current-password" className={inputClass} />
          </Field>
          <Button type="submit" variant="danger" busy={busy === "delete"} disabled={!deletePassword}>Delete my account</Button>
        </form>
      </Card>
    </main>
  );
}
