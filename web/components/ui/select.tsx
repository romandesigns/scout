"use client";
import * as React from "react";
export function Select({value,onValueChange,options,label,className=""}:{value:string;onValueChange:(value:string)=>void;options:{value:string;label:string}[];label:string;className?:string}){return <label className="shadcn-select"><span className="sr-only">{label}</span><select aria-label={label} value={value} onChange={event=>onValueChange(event.target.value)} className={className}>{options.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;}
