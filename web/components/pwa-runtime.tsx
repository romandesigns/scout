"use client";

import { useEffect, useState } from "react";

type InstallPrompt=Event&{prompt:()=>Promise<void>;userChoice:Promise<{outcome:string}>};

export function PwaRuntime(){
  const [install,setInstall]=useState<InstallPrompt|null>(null);
  const [update,setUpdate]=useState<ServiceWorker|null>(null);
  const [online,setOnline]=useState(true);
  useEffect(()=>{
    setOnline(navigator.onLine);
    const onOnline=()=>setOnline(true),onOffline=()=>setOnline(false);
    const onInstall=(event:Event)=>{event.preventDefault();setInstall(event as InstallPrompt);};
    window.addEventListener("online",onOnline);window.addEventListener("offline",onOffline);window.addEventListener("beforeinstallprompt",onInstall);
    if("serviceWorker" in navigator){void navigator.serviceWorker.register("/sw.js").then(registration=>{
      if(registration.waiting)setUpdate(registration.waiting);
      registration.addEventListener("updatefound",()=>registration.installing?.addEventListener("statechange",()=>{if(registration.waiting)setUpdate(registration.waiting);}));
    });}
    return()=>{window.removeEventListener("online",onOnline);window.removeEventListener("offline",onOffline);window.removeEventListener("beforeinstallprompt",onInstall);};
  },[]);
  if(online&&!install&&!update)return null;
  return <div className="pwa-runtime" role="status">
    {!online&&<span>Offline shell · live market data paused</span>}
    {install&&<button onClick={()=>{void install.prompt();void install.userChoice.finally(()=>setInstall(null));}}>Install Scout</button>}
    {update&&<button onClick={()=>{navigator.serviceWorker.addEventListener("controllerchange",()=>window.location.reload(),{once:true});update.postMessage({type:"SKIP_WAITING"});}}>Update available</button>}
  </div>;
}
