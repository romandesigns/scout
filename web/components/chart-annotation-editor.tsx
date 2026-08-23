"use client";

import {useEffect,useRef,useState} from "react";
import {IconArrowBackUp,IconCheck,IconClipboard,IconDownload,IconPencil,IconShare3,IconTrash,IconX} from "@tabler/icons-react";
import {saveDevelopmentAnnotation} from "@/lib/api";
import type {DevelopmentReviewArtifact} from "@/lib/api";
import {Button} from "@/components/ui/button";

type Props={evaluationId:number;ticker:string;src:string;alt:string;onClose:()=>void};
type DrawingTool="pen"|"rectangle"|"square"|"ellipse"|"circle";
type Point={x:number;y:number};

export function ChartAnnotationEditor({evaluationId,ticker,src,alt,onClose}:Props){
  const canvasRef=useRef<HTMLCanvasElement>(null);
  const sourceRef=useRef<ImageBitmap|null>(null);
  const drawing=useRef(false);
  const startPoint=useRef<Point|null>(null);
  const shapePreview=useRef<ImageData|null>(null);
  const history=useRef<ImageData[]>([]);
  const [tool,setTool]=useState<DrawingTool>("pen");
  const [color,setColor]=useState("#ff2d55");
  const [width,setWidth]=useState(5);
  const [notes,setNotes]=useState("");
  const [message,setMessage]=useState("Loading full-resolution chart…");
  const [busy,setBusy]=useState(false);
  const [artifact,setArtifact]=useState<DevelopmentReviewArtifact|null>(null);

  const drawSource=(canvas:HTMLCanvasElement,image:ImageBitmap)=>{
    canvas.width=image.width;canvas.height=image.height;
    canvas.getContext("2d")?.drawImage(image,0,0);
    history.current=[];
  };
  useEffect(()=>{
    let cancelled=false;
    void fetch(src).then(response=>{if(!response.ok)throw new Error("Chart image could not be loaded.");return response.blob();}).then(createImageBitmap).then(image=>{
      if(cancelled){image.close();return;}
      sourceRef.current=image;
      if(canvasRef.current)drawSource(canvasRef.current,image);
      setMessage("Draw directly on the chart. Add context in the analysis note below.");
    }).catch(error=>setMessage(String(error)));
    return()=>{cancelled=true;sourceRef.current?.close();sourceRef.current=null;};
  },[src]);
  useEffect(()=>{const close=(event:KeyboardEvent)=>{if(event.key==="Escape")onClose();};document.addEventListener("keydown",close);return()=>document.removeEventListener("keydown",close);},[onClose]);

  const point=(event:React.PointerEvent<HTMLCanvasElement>)=>{
    const canvas=canvasRef.current!;const rect=canvas.getBoundingClientRect();
    return {x:(event.clientX-rect.left)*canvas.width/rect.width,y:(event.clientY-rect.top)*canvas.height/rect.height};
  };
  const remember=()=>{
    const canvas=canvasRef.current,context=canvas?.getContext("2d");if(!canvas||!context)return;
    history.current=[...history.current.slice(-29),context.getImageData(0,0,canvas.width,canvas.height)];
  };
  const configure=(context:CanvasRenderingContext2D,canvas:HTMLCanvasElement)=>{
    context.strokeStyle=color;context.lineWidth=width*canvas.width/1400;context.lineCap="round";context.lineJoin="round";
  };
  const shapeBounds=(origin:Point,current:Point,constrained:boolean)=>{
    let dx=current.x-origin.x,dy=current.y-origin.y;
    if(constrained){const size=Math.max(Math.abs(dx),Math.abs(dy));dx=Math.sign(dx||1)*size;dy=Math.sign(dy||1)*size;}
    return {x:Math.min(origin.x,origin.x+dx),y:Math.min(origin.y,origin.y+dy),width:Math.abs(dx),height:Math.abs(dy)};
  };
  const drawShape=(context:CanvasRenderingContext2D,origin:Point,current:Point)=>{
    const bounds=shapeBounds(origin,current,tool==="square"||tool==="circle");
    context.beginPath();
    if(tool==="rectangle"||tool==="square")context.rect(bounds.x,bounds.y,bounds.width,bounds.height);
    else context.ellipse(bounds.x+bounds.width/2,bounds.y+bounds.height/2,bounds.width/2,bounds.height/2,0,0,Math.PI*2);
    context.stroke();
  };
  const start=(event:React.PointerEvent<HTMLCanvasElement>)=>{
    const canvas=canvasRef.current;if(!canvas||!sourceRef.current)return;
    remember();drawing.current=true;canvas.setPointerCapture(event.pointerId);
    const p=point(event),context=canvas.getContext("2d")!;
    configure(context,canvas);startPoint.current=p;
    if(tool==="pen"){context.beginPath();context.moveTo(p.x,p.y);}else shapePreview.current=context.getImageData(0,0,canvas.width,canvas.height);
  };
  const move=(event:React.PointerEvent<HTMLCanvasElement>)=>{
    if(!drawing.current)return;const p=point(event),canvas=canvasRef.current,context=canvas?.getContext("2d");if(!canvas||!context)return;
    if(tool==="pen"){context.lineTo(p.x,p.y);context.stroke();return;}
    if(shapePreview.current&&startPoint.current){context.putImageData(shapePreview.current,0,0);configure(context,canvas);drawShape(context,startPoint.current,p);}
  };
  const stop=(event:React.PointerEvent<HTMLCanvasElement>)=>{
    if(!drawing.current)return;
    if(tool!=="pen"&&startPoint.current){const canvas=canvasRef.current,context=canvas?.getContext("2d");if(canvas&&context){if(shapePreview.current)context.putImageData(shapePreview.current,0,0);configure(context,canvas);drawShape(context,startPoint.current,point(event));}}
    drawing.current=false;startPoint.current=null;shapePreview.current=null;
  };
  const undo=()=>{const canvas=canvasRef.current,context=canvas?.getContext("2d"),previous=history.current.pop();if(context&&previous)context.putImageData(previous,0,0);};
  const clear=()=>{if(canvasRef.current&&sourceRef.current){remember();const context=canvasRef.current.getContext("2d");context?.clearRect(0,0,canvasRef.current.width,canvasRef.current.height);context?.drawImage(sourceRef.current,0,0);}};
  const blob=()=>new Promise<Blob>((resolve,reject)=>canvasRef.current?.toBlob(value=>value?resolve(value):reject(new Error("Could not export annotated chart.")),"image/png"));
  const saveAndShare=async()=>{
    if(!canvasRef.current)return;setBusy(true);setMessage("Saving annotated chart for analysis…");
    try{
      const exported=await blob();
      const file=new File([exported],`${ticker}-scout-annotated.png`,{type:"image/png"});
      let shared=false;
      if(navigator.share&&navigator.canShare?.({files:[file]})){
        try{await navigator.share({title:`${ticker} Scout chart analysis`,text:notes||`Analyze this annotated ${ticker} Scout development chart.`,files:[file]});shared=true;}catch(error){if((error as DOMException).name!=="AbortError")throw error;}
      }
      const reader=new FileReader();
      const imageData=await new Promise<string>((resolve,reject)=>{reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(reader.error);reader.readAsDataURL(exported);});
      const artifact=await saveDevelopmentAnnotation(evaluationId,imageData,notes);
      setArtifact(artifact);
      setMessage(`${shared?"Shared and saved":"Saved"} as ${artifact.name}. Copy the Codex request to share the exact chart and evaluation context.`);
    }catch(error){
      const detail=error instanceof TypeError?"Scout's annotation API is unavailable. Restart the Scout backend, then try again.":String(error);
      setMessage(detail);
    }finally{setBusy(false);}
  };
  const copyForCodex=async()=>{
    if(!artifact)return;
    try{await navigator.clipboard.writeText(artifact.share_prompt);setMessage("Copied. Paste that request into our chat and I can open the chart and its evaluation context directly.");}
    catch{setMessage(`Copy this request: ${artifact.share_prompt}`);}
  };
  const download=async()=>{const file=await blob();const url=URL.createObjectURL(file);const anchor=document.createElement("a");anchor.href=url;anchor.download=`${ticker}-scout-annotated.png`;anchor.click();URL.revokeObjectURL(url);};

  return <div className="fixed inset-0 z-50 flex flex-col bg-black/95 p-3 backdrop-blur-sm sm:p-5" role="dialog" aria-modal="true" aria-label="Annotate formation audit chart">
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-white/15 bg-black/80 p-2 text-white">
      <div className="mr-2 flex items-center gap-2 text-sm font-semibold"><IconPencil size={18}/>{ticker} annotations</div>
      <div className="flex flex-wrap gap-1" role="toolbar" aria-label="Drawing shapes">{(["pen","rectangle","square","ellipse","circle"] as DrawingTool[]).map(value=><Button key={value} variant={tool===value?"default":"ghost"} onClick={()=>setTool(value)} aria-pressed={tool===value}>{value[0].toUpperCase()+value.slice(1)}</Button>)}</div>
      <label className="flex items-center gap-2 text-xs">Color <input type="color" value={color} onChange={event=>setColor(event.target.value)} className="h-8 w-10 cursor-pointer rounded border-0 bg-transparent"/></label>
      <label className="flex items-center gap-2 text-xs">Stroke <input type="range" min="2" max="16" value={width} onChange={event=>setWidth(Number(event.target.value))}/></label>
      <Button variant="ghost" onClick={undo}><IconArrowBackUp size={16}/> Undo</Button>
      <Button variant="ghost" onClick={clear}><IconTrash size={16}/> Clear marks</Button>
      <Button variant="ghost" onClick={()=>void download()}><IconDownload size={16}/> Download</Button>
      <Button className="ml-auto" disabled={busy} onClick={()=>void saveAndShare()}><IconShare3 size={16}/>{busy?"Saving…":"Share for analysis"}</Button>
      {artifact&&<Button variant="ghost" onClick={()=>void copyForCodex()}><IconClipboard size={16}/>Copy for Codex</Button>}
      <button type="button" className="rounded-md p-2 hover:bg-white/10" onClick={onClose} aria-label="Close annotation editor"><IconX size={22}/></button>
    </div>
    <div className="min-h-0 flex-1 overflow-auto text-center"><canvas ref={canvasRef} aria-label={alt} className="inline-block h-auto max-w-none cursor-crosshair touch-none rounded bg-white shadow-2xl" style={{width:"min(1800px, 94vw)"}} onPointerDown={start} onPointerMove={move} onPointerUp={stop} onPointerCancel={stop}/></div>
    <div className="mx-auto mt-3 grid w-full max-w-5xl gap-2 sm:grid-cols-[1fr_auto]">
      <textarea value={notes} onChange={event=>setNotes(event.target.value)} placeholder="Analysis note: what should I inspect, compare, or explain?" className="min-h-16 rounded-md border border-white/20 bg-black/70 p-3 text-sm text-white outline-none focus:border-[var(--blue)]" maxLength={4000}/>
      <div className="flex min-w-64 items-center gap-2 rounded-md border border-white/15 bg-black/70 px-3 py-2 text-xs text-white/75"><IconCheck className="shrink-0 text-[#2ed6a1]" size={17}/>{message}</div>
    </div>
  </div>;
}
