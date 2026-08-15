import * as React from "react";
import { cn } from "@/lib/utils";

type Props = React.ComponentProps<"button"> & { variant?: "default" | "ghost" | "outline" | "danger" };

export function Button({ className, variant = "default", ...props }: Props) {
  return <button className={cn("scout-button", `scout-button-${variant}`, className)} {...props} />;
}
