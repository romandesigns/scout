"use client";

import { Switch as BaseSwitch } from "@base-ui/react/switch";
import { cn } from "@/lib/utils";

type SwitchProps = Omit<React.ComponentProps<typeof BaseSwitch.Root>, "className"> & {
  className?: string;
};

export function Switch({ className, ...props }: SwitchProps) {
  return (
    <BaseSwitch.Root className={cn("scout-switch", className)} {...props}>
      <BaseSwitch.Thumb className="scout-switch-thumb" />
    </BaseSwitch.Root>
  );
}
