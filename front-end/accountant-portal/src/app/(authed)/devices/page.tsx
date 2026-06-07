"use client"

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { Topbar } from "@/components/shell/Topbar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/lib/use-toast"
import { useAuth } from "@/lib/auth/context"
import { MonitorSmartphone, Laptop, Monitor, RotateCcw } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import type { AccountantDevice } from "@/types/api"

function PlatformIcon({ platform }: { platform: string }) {
  if (platform === "macos") return <Laptop className="h-4 w-4 text-zinc-400" />
  if (platform === "windows") return <Monitor className="h-4 w-4 text-zinc-400" />
  return <MonitorSmartphone className="h-4 w-4 text-zinc-400" />
}

export default function DevicesPage() {
  const { toast } = useToast()
  const { deviceId: currentDeviceId } = useAuth()
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.listDevices(),
  })

  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeDevice(id, { reason: "revoked_by_user" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] })
      toast({ title: "Device revoked", description: "That device can no longer access the portal.", variant: "destructive" })
    },
    onError: () => {
      toast({ title: "Revoke failed", description: "Please try again.", variant: "destructive" })
    },
  })

  return (
    <>
      <Topbar title="Devices" />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mb-6">
          <h2 className="text-base font-semibold text-zinc-100">Registered Devices</h2>
          <p className="mt-1 text-sm text-zinc-500">
            Devices that have signed in to your account. Revoke any you don't recognise.
          </p>
        </div>

        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => <Skeleton key={i} className="h-20 w-full rounded-xl" />)}
          </div>
        ) : (
          <div className="space-y-3">
            {(data?.devices ?? []).map((device: AccountantDevice) => (
              <div
                key={device.id}
                className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900 px-5 py-4"
              >
                <div className="flex items-center gap-4">
                  <PlatformIcon platform={device.platform} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-zinc-100">{device.device_label}</span>
                      {device.is_current_device && (
                        <Badge variant="indigo" className="text-[10px]">This device</Badge>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-zinc-500">
                      {device.platform} · {device.use_count} sign-in{device.use_count !== 1 ? "s" : ""} ·{" "}
                      last seen {formatDistanceToNow(new Date(device.last_seen_at), { addSuffix: true })}
                    </p>
                  </div>
                </div>

                {!device.is_current_device && (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => revoke.mutate(device.id)}
                    disabled={revoke.isPending}
                  >
                    {revoke.isPending ? <RotateCcw className="h-3 w-3 animate-spin" /> : "Revoke"}
                  </Button>
                )}
              </div>
            ))}

            {data?.devices?.length === 0 && (
              <p className="text-sm text-zinc-500 text-center py-12">No devices registered yet.</p>
            )}
          </div>
        )}
      </main>
    </>
  )
}
