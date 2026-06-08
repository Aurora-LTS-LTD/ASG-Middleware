"use client";

import { useState } from "react";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { onboarding, ApiClientError } from "@/lib/api/client";
import { LEGAL_STRUCTURE_LABELS } from "@/types/onboarding";
import type { IdentityPayload, LegalStructure } from "@/types/onboarding";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { StepCard, ErrorBanner, StepProps, inputClass } from "./common";

const schema = z.object({
  first_name: z.string().min(2, "Required").max(80),
  last_name: z.string().min(2, "Required").max(80),
  legal_structure: z.enum(["osek_morshe", "osek_patur", "chevra_baam"]),
  tax_id: z.string().min(5, "Enter a valid tax / business ID").max(20),
  display_name: z.string().min(3, "Enter your business name").max(120),
  business_address: z.string().max(200).optional().or(z.literal("")),
  city: z.string().max(80).optional().or(z.literal("")),
  postal_code: z.string().max(20).optional().or(z.literal("")),
  business_phone: z.string().max(30).optional().or(z.literal("")),
});

export function IdentityStep({ token, onAdvance }: StepProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      first_name: "", last_name: "", legal_structure: "osek_morshe",
      tax_id: "", display_name: "", business_address: "", city: "", postal_code: "", business_phone: "",
    },
  });

  async function onSubmit(values: z.infer<typeof schema>) {
    setLoading(true);
    setError(null);
    try {
      const payload: IdentityPayload = {
        first_name: values.first_name,
        last_name: values.last_name,
        legal_structure: values.legal_structure as LegalStructure,
        tax_id: values.tax_id,
        display_name: values.display_name,
        business_address: values.business_address || undefined,
        city: values.city || undefined,
        postal_code: values.postal_code || undefined,
        business_phone: values.business_phone || undefined,
      };
      const res = await onboarding.identity(token, payload);
      onAdvance(res.current_step);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Couldn't save your details." : "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <StepCard title="Business details" description="Tell us about you and your business. This becomes your tax profile.">
      <ErrorBanner message={error} />
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormField control={form.control} name="first_name" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">First name</FormLabel>
                <FormControl><Input {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
            <FormField control={form.control} name="last_name" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Last name</FormLabel>
                <FormControl><Input {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
          </div>

          <FormField control={form.control} name="display_name" render={({ field }) => (
            <FormItem>
              <FormLabel className="text-foreground">Business name</FormLabel>
              <FormControl><Input placeholder="As it appears on invoices" {...field} className={inputClass} /></FormControl>
              <FormMessage className="text-red-400" />
            </FormItem>
          )} />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormField control={form.control} name="legal_structure" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Legal structure</FormLabel>
                <FormControl>
                  <select
                    {...field}
                    className={`flex h-10 w-full rounded-md border px-3 py-2 text-sm ${inputClass}`}
                  >
                    {(Object.keys(LEGAL_STRUCTURE_LABELS) as LegalStructure[]).map((k) => (
                      <option key={k} value={k}>{LEGAL_STRUCTURE_LABELS[k]}</option>
                    ))}
                  </select>
                </FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
            <FormField control={form.control} name="tax_id" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Tax / business ID</FormLabel>
                <FormControl><Input placeholder="9-digit ID" {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormField control={form.control} name="business_address" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Address <span className="text-muted-foreground">(optional)</span></FormLabel>
                <FormControl><Input {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
            <FormField control={form.control} name="city" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">City <span className="text-muted-foreground">(optional)</span></FormLabel>
                <FormControl><Input {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormField control={form.control} name="postal_code" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Postal code <span className="text-muted-foreground">(optional)</span></FormLabel>
                <FormControl><Input {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
            <FormField control={form.control} name="business_phone" render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Business phone <span className="text-muted-foreground">(optional)</span></FormLabel>
                <FormControl><Input placeholder="+972…" {...field} className={inputClass} /></FormControl>
                <FormMessage className="text-red-400" />
              </FormItem>
            )} />
          </div>

          <Button type="submit" className="w-full bg-indigo-600 text-white hover:bg-indigo-700" disabled={loading}>
            {loading ? "Saving…" : "Continue"}
          </Button>
        </form>
      </Form>
    </StepCard>
  );
}
