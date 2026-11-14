import React,{useEffect,useState,useRef} from "react";
const API=import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const menu=["Book Site Visit","Request Brochure","Call Me Back"];
const merge=(previous,rows)=>[...new Map([...previous,...rows].map(x=>[x.id,x])).values()].sort((a,b)=>a.id-b.id);
const dateLabel=value=>new Date(value).toLocaleString("en-IN");

export default function SupportChat({token,admin=false,initialCustomerId=null}){
 const [threads,setThreads]=useState([]),[selected,setSelected]=useState(initialCustomerId),[messages,setMessages]=useState([]),[draft,setDraft]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[older,setOlder]=useState(false);
 const [quick,setQuick]=useState(menu),[inProgress,setInProgress]=useState(false),[aiAvailable,setAiAvailable]=useState(false),[aiConsent,setAiConsent]=useState(false),[mode,setMode]=useState(null),[refreshing,setRefreshing]=useState(false),[lastUpdated,setLastUpdated]=useState(null),[notice,setNotice]=useState("");
 const attempt=useRef(null), historyRef=useRef(null), refreshRef=useRef(null), operation=useRef(false), scrollBottom=useRef(true),version=useRef(0);
 const path=admin?selected?"/api/admin/messages/"+selected:null:"/api/customer/messages";
 const selectedThread=threads.find(t=>t.customer_id===selected);
 useEffect(()=>{if(historyRef.current&&scrollBottom.current)historyRef.current.scrollTop=historyRef.current.scrollHeight},[messages,selected]);
 function applyState(state){setQuick(state.quick);setInProgress(state.in_progress);setAiAvailable(Boolean(state.ai_available));setMode(state.mode||"assistant");if(state.mode==="team")setAiConsent(false);}
 async function api(path,body){
   const res=await fetch(API+path,{method:body?"POST":"GET",credentials:"include",signal:AbortSignal.timeout(25000),headers:{"Content-Type":"application/json","X-Session-Panel":admin?"admin":"customer"},...(body?{body:JSON.stringify(body)}:{})});
   const data=await res.json().catch(()=>({}));
   if(!res.ok)throw new Error(typeof data.detail==="string"?data.detail:"Unable to complete request. Please try again.");
   return data;
 }
 useEffect(()=>{
   let active=true, inFlight=false;
   async function refresh(manual=false){
     if((document.hidden&&!manual)||inFlight||operation.current)return;
     inFlight=true;const requestVersion=version.current;if(manual)setRefreshing(true);
     try{
       const tasks=[];
       if(admin)tasks.push(api("/api/admin/messages?limit=200").then(rows=>{if(active&&requestVersion===version.current)setThreads(rows)}));
       else tasks.push(api("/api/customer/chat-state").then(state=>{if(active&&requestVersion===version.current)applyState(state)}));
       if(path)tasks.push(api(path+"?limit=50").then(rows=>{if(active&&requestVersion===version.current){setMessages(prev=>merge(prev,rows));setOlder(prev=>prev||rows.length===50);}}));
       const results=await Promise.allSettled(tasks);
       if(!active||requestVersion!==version.current)return;
       const failure=results.find(x=>x.status==="rejected");
       if(failure)throw failure.reason;
       setError("");setLastUpdated(new Date());if(manual)setNotice("Messages refreshed.");
     }catch(e){if(active&&requestVersion===version.current)setError(e.message);}
     finally{inFlight=false;if(active)setRefreshing(false);}
   }
   setMessages([]);setOlder(false);setDraft("");setError("");setNotice("");setLastUpdated(null);setRefreshing(false);attempt.current=null;scrollBottom.current=true;
   refreshRef.current=refresh;refresh();const timer=setInterval(()=>refresh(),10000);const visible=()=>refresh();document.addEventListener("visibilitychange",visible);
   return()=>{active=false;clearInterval(timer);document.removeEventListener("visibilitychange",visible);if(refreshRef.current===refresh)refreshRef.current=null;};
 },[token,admin,selected]);
 async function changeMode(next){
   if(admin||operation.current||refreshing||mode===next)return;
   operation.current=true;version.current++;setBusy(true);setError("");setNotice("");
   try{
     await api("/api/customer/chat-mode",{mode:next});
     applyState(await api("/api/customer/chat-state"));
     attempt.current=null;
     setNotice(next==="team"?"Property team selected. Your next message goes directly to our team.":"Automated assistant selected.");
   }catch(e){setError(e.message)}finally{operation.current=false;setBusy(false);}
 }
 async function send(e,quickBody=null){
   e.preventDefault();const body=(quickBody??draft).trim();if(operation.current||refreshing||!path||!body||(!admin&&!mode))return;
   const consent=!admin&&mode==="assistant"&&!quickBody&&aiConsent;
   if(!attempt.current||attempt.current.body!==body||attempt.current.ai_consent!==consent)attempt.current={body,client_id:crypto.randomUUID(),ai_consent:consent};
   operation.current=true;version.current++;setBusy(true);setError("");setNotice("");
   try{
     const saved=await api(path,attempt.current);
     attempt.current=null;if(!quickBody)setDraft("");
     scrollBottom.current=true;setMessages(prev=>merge(prev,[saved]));
     setNotice(admin?"Reply sent. The conversation is now with the property team.":mode==="team"?"Message sent to our property team.":"Message sent.");
     const tasks=[api(path+"?limit=50").then(rows=>setMessages(prev=>merge(prev,rows)))];
     if(admin)tasks.push(api("/api/admin/messages?limit=200").then(setThreads));else tasks.push(api("/api/customer/chat-state").then(applyState));
     const results=await Promise.allSettled(tasks);
     if(results.some(x=>x.status==="rejected"))setError("Message saved, but the inbox could not fully refresh. Use Refresh to retry.");
     else setLastUpdated(new Date());
   }catch(e){setError(e.message)}finally{operation.current=false;setBusy(false);}
 }
 async function loadOlder(){
   if(!messages.length||!path||operation.current||refreshing)return;
   operation.current=true;version.current++;setBusy(true);setError("");
   try{
     const el=historyRef.current, previousHeight=el?.scrollHeight||0, previousTop=el?.scrollTop||0;
     scrollBottom.current=false;
     const rows=await api(path+"?before_id="+messages[0].id+"&limit=50");
     setMessages(prev=>merge(rows,prev));setOlder(rows.length===50);
     requestAnimationFrame(()=>{if(el)el.scrollTop=previousTop+el.scrollHeight-previousHeight;});
   }catch(e){setError(e.message)}finally{operation.current=false;setBusy(false);}
 }
 return <section className="public-card support-chat" aria-label={admin?"Customer messages":"Private messages"}>
 <div className="section-head support-heading"><div><span className="tag">PRIVATE SUPPORT</span><h2>{admin?"Customer messages":"Chat with MetroEstate"}</h2><p className="muted">{admin?"Manage private enquiries and respond as the property team.":"Choose instant automated guidance or speak directly with our property team."}</p></div><button type="button" className="outline" disabled={busy||refreshing} onClick={()=>refreshRef.current?.(true)}>{refreshing?"Refreshing…":"Refresh"}</button></div>
 <p className="muted tiny" role="status">{refreshing?"Updating conversation…":lastUpdated?"Last updated "+dateLabel(lastUpdated):"Connecting to your inbox…"}</p>
 {notice&&<p role="status" className="notice">{notice}</p>}
 {error&&<div role="alert" className="notice">{error}</div>}
 {!admin&&<div className="support-mode" aria-label="Choose support channel"><button type="button" className={"support-option "+(mode==="assistant"?"selected":"")} aria-pressed={mode==="assistant"} disabled={busy||refreshing||!mode} onClick={()=>changeMode("assistant")}><strong>Automated assistant — Riya</strong><small>Instant property guidance</small></button><button type="button" className={"support-option "+(mode==="team"?"selected":"")} aria-pressed={mode==="team"} disabled={busy||refreshing||!mode} onClick={()=>changeMode("team")}><strong>Property team</strong><small>Personal support from our staff</small></button></div>}
 <div className={admin?"support-workspace":""}>
 {admin&&<div className="support-threads"><h3>Conversations</h3><p className="muted tiny">Pending team messages appear first.</p>{threads.length?[...threads].sort((a,b)=>Number(b.team_pending)-Number(a.team_pending)).map(t=><button type="button" disabled={busy} key={t.customer_id} aria-pressed={selected===t.customer_id} className={"outline support-thread "+(selected===t.customer_id?"selected":"")} onClick={()=>setSelected(t.customer_id)}><strong>{t.name}</strong><small>{t.email}</small><span className={t.team_pending?"support-pending":""}>{t.team_pending?"New · Awaiting team reply":t.mode==="team"?"Property team · Replied":"Automated support"}</span><span className="thread-preview">{t.last_message}</span></button>):<p>No customer conversations yet.</p>}</div>}
 <div className="support-conversation">
 {path?<>{admin&&selectedThread&&<div className="support-mode-note"><strong>{selectedThread.name}</strong><span>{selectedThread.mode==="team"?"Human-only conversation":"Automated support · Your reply takes over"}</span></div>}
 {!admin&&mode==="team"&&<div className="support-mode-note"><strong>Connected to the property team</strong><span>Send your message here. Automated replies are paused; a team member will respond personally.</span></div>}
 {!admin&&mode==="assistant"&&<div className="support-quick-actions" aria-label="Quick property requests"><p>{inProgress?"Continue your request":"How can we help?"}</p><div className="support-action-grid">{[...new Set([...quick,...(inProgress?["Cancel"]:[])])].map(label=><button type="button" key={label} className="outline" disabled={busy||refreshing} onClick={e=>send(e,label)}>{label}</button>)}</div><small>Requests are not confirmed until our property team responds.</small></div>}
 <div className="support-history" ref={historyRef} role="log" aria-label="Conversation history" aria-live="polite" onScroll={e=>{const x=e.currentTarget;scrollBottom.current=x.scrollHeight-x.scrollTop-x.clientHeight<80;}}>{older&&<button className="outline" type="button" disabled={busy||refreshing} onClick={loadOlder}>Load older messages</button>}{messages.length?messages.map(m=><article key={m.id} className={"support-bubble "+m.sender}><strong>{m.sender==="assistant"?"Automated assistant":m.sender==="admin"?"Property team":admin?"Customer":"You"}</strong><p>{m.body}</p><small>{dateLabel(m.created_at)}</small></article>):<p className="muted">No messages yet. Start a private conversation.</p>}</div>
 {busy&&<p className="chat-typing" role="status">Working…</p>}
 <form className="public-form" onSubmit={send}><label>{admin?"Reply to customer":"Your message"}<textarea value={draft} onChange={e=>setDraft(e.target.value)} rows="3" maxLength="2000" required placeholder={admin?"Write a personal reply":mode==="team"?"Message our property team":"Type your question"} disabled={busy}/></label>{!admin&&mode==="assistant"&&aiAvailable&&<label className="ai-consent"><input type="checkbox" checked={aiConsent} onChange={e=>setAiConsent(e.target.checked)} disabled={busy}/> Use AI to answer my questions (this question and up to 14 recent messages between you and the automated assistant may be sent to an external AI provider; avoid personal information).</label>}<button className="primary" disabled={busy||refreshing||!draft.trim()||(!admin&&!mode)}>{busy?"Working…":"Send message"}</button></form><p className="muted tiny">Messages refresh automatically. Do not share passwords, payment card details or identity documents here.</p></>:<p className="support-empty">Select a conversation to read and reply.</p>}
 </div></div></section>
}
