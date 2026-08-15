"use client";

import { useEffect, useState } from "react";

type InstallPrompt=Event&{prompt:()=>Promise<void>;userChoice:Promise<{outcome:string}>};

export function PwaRuntime(){
  const [install,setInstall]=useState<InstallPrompt|null>(null);
  const [update,setUpdate]=useState<ServiceWorker|null>(null);
  const [online,setOnline]=useState(true);
  useEffect(()=>{
    const nativeShell="__TAURI_INTERNALS__" in window||"__TAURI__" in window||window.location.hostname==="tauri.localhost";
    setOnline(navigator.onLine);
    const onOnline=()=>setOnline(true),onOffline=()=>setOnline(false);
    const onInstall=(event:Event)=>{event.preventDefault();setInstall(event as InstallPrompt);};
    window.addEventListener("online",onOnline);window.addEventListener("offline",onOffline);window.addEventListener("beforeinstallprompt",onInstall);
    if(nativeShell&&"serviceWorker" in navigator){
      // Native releases ship their assets with the executable. Remove any
      // service worker left by pre-6.1 builds so it cannot mix release files.
      void navigator.serviceWorker.getRegistrations().then(items=>Promise.all(items.map(item=>item.unregister())));
      if("caches" in window)void caches.keys().then(keys=>Promise.all(keys.map(key=>caches.delete(key))));
    }else if("serviceWorker" in navigator){void navigator.serviceWorker.register("/sw.js").then(registration=>{
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
