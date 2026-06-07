"use client";

/**
 * Settings — Profile (editable name + firm, live to M1), Security (change
 * password + device management), and Appearance (light/dark/system + language).
 * Fully token-based so both themes render correctly.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTheme } from "next-themes";
import { User as UserIcon, ShieldCheck, Palette, Sun, Moon, Monitor, MonitorSmartphone, Check } from "lucide-react";

import { Topbar } from "@/components/shell/Topbar";
import { LocaleSwitcher } from "@/components/shell/LocaleSwitcher";
import { useAuth } from "@/lib/auth/context";
import { api, ApiClientError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";

const inputClass = "bg-background border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-ring";

const TABS = [
  { id: "profile", label: "Profile", Icon: UserIcon },
  { id: "security", label: "Security", Icon: ShieldCheck },
  { id: "appearance", label: "Appearance", Icon: Palette },
] as const;
type TabId = (typeof TABS)[number]["id"];

const profileSchema = z.object({
  name: z.string().min(1, { message: "Name is required." }).max(200),
  firm_name: z.string().max(200),
});

const passwordSchema = z
  .object({
    old_password: z.string().min(1, { message: "Enter your current password." }),
    new_password: z
      .string()
      .min(10, { message: "Password must be at least 10 characters." })
      .regex(/[A-Za-z]/, { message: "Include at least one letter." })
      .regex(/\d/, { message: "Include at least one number." }),
    confirm: z.string(),
  })
  .refine((d) => d.new_password === d.confirm, { message: "Passwords don't match.", path: ["confirm"] });

export default function SettingsPage() {
  const [tab, setTab] = useState<TabId>("profile");
  return (
    <>
      <Topbar title="Settings" />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl">
          <div className="mb-6 flex gap-1 rounded-lg border border-border bg-card p-1">
            {TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  tab === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </button>
            ))}
          </div>

          {tab === "profile" && <ProfileTab />}
          {tab === "security" && <SecurityTab />}
          {tab === "appearance" && <AppearanceTab />}
        </div>
      </main>
    </>
  );
}

function ProfileTab() {
  const { user, updateProfile } = useAuth();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<z.infer<typeof profileSchema>>({
    resolver: zodResolver(profileSchema),
    defaultValues: { name: user?.name ?? "", firm_name: user?.firm_name ?? "" },
  });

  async function onSubmit(values: z.infer<typeof profileSchema>) {
    setSaved(false);
    setError(null);
    try {
      await updateProfile({ name: values.name.trim(), firm_name: values.firm_name.trim() });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || err.message : "Couldn't save. Please try again.");
    }
  }

  return (
    <Card className="border-border bg-card">
      <CardHeader>
        <CardTitle className="text-base text-foreground">Profile</CardTitle>
        <CardDescription className="text-muted-foreground">Your name and firm, shown across the portal.</CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-foreground">Full name</FormLabel>
                  <FormControl>
                    <Input {...field} onChange={(e) => { field.onChange(e); setSaved(false); }} className={inputClass} />
                  </FormControl>
                  <FormMessage className="text-red-400" />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="firm_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-foreground">Firm</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="Your accounting firm" onChange={(e) => { field.onChange(e); setSaved(false); }} className={inputClass} />
                  </FormControl>
                  <FormMessage className="text-red-400" />
                </FormItem>
              )}
            />

            {/* Read-only identity fields */}
            <div className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-background p-3 text-sm">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Email</div>
                <div className="mt-0.5 truncate text-foreground">{user?.email ?? "—"}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Role</div>
                <div className="mt-0.5 font-mono text-xs uppercase tracking-wider text-foreground">{user?.role ?? "—"}</div>
              </div>
            </div>

            {error && <p className="text-sm text-red-400">{error}</p>}
            <div className="flex items-center gap-3">
              <Button type="submit" className="bg-indigo-600 hover:bg-indigo-700 text-white" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Saving…" : "Save changes"}
              </Button>
              {saved && (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-400">
                  <Check className="h-3.5 w-3.5" /> Saved
                </span>
              )}
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function SecurityTab() {
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<z.infer<typeof passwordSchema>>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { old_password: "", new_password: "", confirm: "" },
  });

  async function onSubmit(values: z.infer<typeof passwordSchema>) {
    setSaved(false);
    setError(null);
    try {
      await api.changePassword({ old_password: values.old_password, new_password: values.new_password });
      setSaved(true);
      form.reset();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || err.message : "Couldn't change password. Please try again.");
    }
  }

  return (
    <div className="space-y-4">
      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-base text-foreground">Change password</CardTitle>
          <CardDescription className="text-muted-foreground">
            Updating your password signs you out on every other device.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
              {(["old_password", "new_password", "confirm"] as const).map((name) => (
                <FormField
                  key={name}
                  control={form.control}
                  name={name}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-foreground">
                        {name === "old_password" ? "Current password" : name === "new_password" ? "New password" : "Confirm new password"}
                      </FormLabel>
                      <FormControl>
                        <Input type="password" autoComplete={name === "old_password" ? "current-password" : "new-password"} placeholder="••••••••" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
              ))}
              {error && <p className="text-sm text-red-400">{error}</p>}
              <div className="flex items-center gap-3">
                <Button type="submit" className="bg-indigo-600 hover:bg-indigo-700 text-white" disabled={form.formState.isSubmitting}>
                  {form.formState.isSubmitting ? "Updating…" : "Update password"}
                </Button>
                {saved && (
                  <span className="inline-flex items-center gap-1 text-sm text-emerald-400">
                    <Check className="h-3.5 w-3.5" /> Password updated
                  </span>
                )}
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>

      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-base text-foreground">Devices</CardTitle>
          <CardDescription className="text-muted-foreground">Review and revoke the devices bound to your account.</CardDescription>
        </CardHeader>
        <CardContent>
          <Link
            href="/devices"
            className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground transition-colors hover:bg-accent"
          >
            <MonitorSmartphone className="h-4 w-4" />
            Manage devices
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}

function AppearanceTab() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const options = [
    { id: "light", label: "Light", Icon: Sun },
    { id: "dark", label: "Dark", Icon: Moon },
    { id: "system", label: "System", Icon: Monitor },
  ] as const;

  return (
    <div className="space-y-4">
      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-base text-foreground">Theme</CardTitle>
          <CardDescription className="text-muted-foreground">Dark is the default. Choose what suits your workspace.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3">
            {options.map(({ id, label, Icon }) => {
              const active = mounted && theme === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTheme(id)}
                  className={`flex flex-col items-center gap-2 rounded-lg border p-4 transition-colors ${
                    active ? "border-primary bg-accent text-foreground" : "border-border bg-background text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Icon className="h-5 w-5" />
                  <span className="text-sm font-medium">{label}</span>
                  {active && <Check className="h-3.5 w-3.5 text-primary" />}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-base text-foreground">Language</CardTitle>
          <CardDescription className="text-muted-foreground">Interface language (English · עברית · العربية).</CardDescription>
        </CardHeader>
        <CardContent>
          <LocaleSwitcher />
        </CardContent>
      </Card>
    </div>
  );
}
