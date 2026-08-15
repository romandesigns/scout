"use client";

import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import { cn } from "@/lib/utils";

export const Tabs = BaseTabs.Root;
type TabsListProps = Omit<React.ComponentProps<typeof BaseTabs.List>, "className"> & { className?: string };
type TabsTriggerProps = Omit<React.ComponentProps<typeof BaseTabs.Tab>, "className"> & { className?: string };
type TabsContentProps = Omit<React.ComponentProps<typeof BaseTabs.Panel>, "className"> & { className?: string };

export function TabsList({ className, ...props }: TabsListProps) {
  return <BaseTabs.List className={cn("scout-tabs-list", className)} {...props} />;
}
export function TabsTrigger({ className, ...props }: TabsTriggerProps) {
  return <BaseTabs.Tab className={cn("scout-tabs-trigger", className)} {...props} />;
}
export function TabsContent({ className, ...props }: TabsContentProps) {
  return <BaseTabs.Panel className={cn("scout-tabs-content", className)} {...props} />;
}
