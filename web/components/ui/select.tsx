"use client";

import * as React from "react";
import { Select as BaseSelect } from "@base-ui/react/select";
import { IconCheck, IconChevronDown } from "@tabler/icons-react";

export function Select({
  value,
  onValueChange,
  options,
  label,
  className = "",
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  className?: string;
}) {
  return (
    <BaseSelect.Root value={value} onValueChange={(next) => next != null && onValueChange(String(next))} items={options}>
      <BaseSelect.Trigger aria-label={label} className={`shadcn-select-trigger ${className}`}>
        <BaseSelect.Value />
        <BaseSelect.Icon className="shadcn-select-icon"><IconChevronDown size={13} /></BaseSelect.Icon>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner sideOffset={6} alignItemWithTrigger={false} className="shadcn-select-positioner">
          <BaseSelect.Popup className="shadcn-select-popup">
            <BaseSelect.List>
              {options.map((option) => (
                <BaseSelect.Item key={option.value} value={option.value} className="shadcn-select-item">
                  <BaseSelect.ItemText>{option.label}</BaseSelect.ItemText>
                  <BaseSelect.ItemIndicator className="shadcn-select-check"><IconCheck size={13} /></BaseSelect.ItemIndicator>
                </BaseSelect.Item>
              ))}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  );
}
