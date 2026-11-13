import React,{useEffect,useState,useRef} from "react";
const API=import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export default function SupportChat({token,admin=false}){
 const [threads,setThreads]=useState([]),[selected,setSelected]=useState(null),[messages,setMessages]=useState([]),[draft,setDraft]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[older,setOlder]=useState(false);
 const attempt=useRef(null);
 const menu=["Book Site Visit","Request Brochure","Call Me Back"];
 const [quick,setQuick]=useState(menu),[inProgress,setInProgress]=useState(false),[aiAvailable,setAiAvailable]=useState(false),[aiConsent,setAiConsent]=useState(false);
 const historyRef=useRef(null);
 useEffect(()=>{if(historyRef.current)historyRef.current.scrollTop=historyRef.current.scrollHeight},[messages.length,selected]);
 async function refreshState(){const state=await api("/api/customer/chat-state");setQuick(state.quick);setInProgress(state.in_progress);setAiAvailable(Boolean(state.ai_available));}
 const path=admin?selected?"/api/admin/messages/"+selected:null:"/api/customer/messages";
 async function api(path,body){
   const res=await fetch(API+path,{method:body?"POST":"GET",credentials:"include",headers:{"Content-Type":"application/json","X-Session-Panel":admin?"admin":"customer"},...(body?{body:JSON.stringify(body)}:{})});
   const data=await res.json().catch(()=>({}));
   if(!res.ok)throw new Error(typeof data.detail==="string"?data.detail:"Unable to complete request. Please try again.");
   return data;
 }
 useEffect(()=>{
   let active=true;
   async function refresh(){
     try{
       if(admin){const rows=await api("/api/admin/messages");if(active)setThreads(rows);}
       if(!admin){const state=await api("/api/customer/chat-state");if(active){setQuick(state.quick);setInProgress(state.in_progress);setAiAvailable(Boolean(state.ai_available));}}
       if(path){const rows=await api(path+"?limit=50");if(active){setMessages(prev=>{const merged=new Map(prev.map(x=>[x.id,x]));rows.forEach(x=>merged.set(x.id,x));return [...merged.values()].sort((a,b)=>a.id-b.id)});setOlder(prev=>prev||rows.length===50);}}
     }catch(e){if(active)setError(e.message);}
   }
   setMessages([]);setOlder(false);setDraft("");setError("");attempt.current=null;
   refresh();const timer=setInterval(refresh,10000);return()=>{active=false;clearInterval(timer);};
 },[token,admin,selected]);
 async function send(e,quickBody=null){
   e.preventDefault();const body=(quickBody??draft).trim();if(busy||!path||!body)return;
   if(!attempt.current||attempt.current.body!==body)attempt.current={body,client_id:crypto.randomUUID(),ai_consent:!admin&&!quickBody&&aiConsent};
   setBusy(true);setError("");
   try{
     await api(path,attempt.current);
     attempt.current=null;if(!quickBody)setDraft("");
     const rows=await api(path+"?limit=50");
     setMessages(prev=>{const merged=new Map(prev.map(x=>[x.id,x]));rows.forEach(x=>merged.set(x.id,x));return [...merged.values()].sort((a,b)=>a.id-b.id)});
     if(admin)setThreads(await api("/api/admin/messages"));else await refreshState();
   }catch(e){setError(e.message)}finally{setBusy(false)}
 }
 async function loadOlder(){
   if(!messages.length||!path||busy)return;
   setBusy(true);
   try{const rows=await api(path+"?before_id="+messages[0].id+"&limit=50");setMessages(prev=>{const merged=new Map([...rows,...prev].map(x=>[x.id,x]));return [...merged.values()].sort((a,b)=>a.id-b.id)});setOlder(rows.length===50)}catch(e){setError(e.message)}finally{setBusy(false)}
 }
 return <section className="public-card support-chat" aria-label={admin?"Customer messages":"Private messages"}><span className="tag">PRIVATE SUPPORT</span><h2>{admin?"Customer messages":"Chat with MetroEstate"}</h2><p>{admin?"Read and reply to customer conversations. Only administrators have access to this inbox.":"Send a question about plots, pricing or site visits. The automated assistant remembers your request and asks one question at a time. Ask a question in your own words, or choose an action. Our property team can reply here."}</p>
 {admin&&<div className="support-threads"><h3>Conversations</h3>{threads.length?threads.map(t=><button type="button" disabled={busy} key={t.customer_id} aria-pressed={selected===t.customer_id} className={"outline support-thread "+(selected===t.customer_id?"selected":"")} onClick={()=>{setSelected(t.customer_id)}}><strong>{t.name}</strong><small>{t.email}</small><span>{t.awaiting_reply?"Awaiting team reply":"Replied"}</span><span className="thread-preview">{t.last_message}</span></button>):<p>No customer conversations yet.</p>}</div>}
 {error&&<div role="alert" className="notice">{error}</div>}
 {path?<>{!admin&&<div className="support-quick-actions" aria-label="Quick property requests"><p>{inProgress?"Continue your request":"How can we help?"}</p><div className="support-action-grid">{[...new Set([...quick,...(inProgress?["Cancel"]:[])])].map(label=><button type="button" key={label} className="outline" disabled={busy} onClick={e=>send(e,label)}>{label}</button>)}</div><small>Requests are saved for our property team. A booking, brochure or callback is not confirmed until the team responds.</small></div>}<div className="support-history" ref={historyRef} role="log" aria-label="Conversation history" aria-live="polite">{older&&<button className="outline" type="button" disabled={busy} onClick={loadOlder}>Load older messages</button>}{messages.length?messages.map(m=><article key={m.id} className={"support-bubble "+m.sender}><strong>{m.sender==="assistant"?"Automated assistant":m.sender==="admin"?"Property team":admin?"Customer":"You"}</strong><p>{m.body}</p><small>{new Date(m.created_at).toLocaleString("en-IN")}</small></article>):<p className="muted">No messages yet. Start a private conversation.</p>}</div>{busy&&<p className="chat-typing" role="status">{admin?"Sending reply…":"Automated assistant is responding…"}</p>}<form className="public-form" onSubmit={send}><label>{admin?"Reply to customer":"Your message"}<textarea value={draft} onChange={e=>setDraft(e.target.value)} rows="3" maxLength="2000" required placeholder="Type your message" disabled={busy}/></label>{!admin&&aiAvailable&&<label className="ai-consent"><input type="checkbox" checked={aiConsent} onChange={e=>setAiConsent(e.target.checked)} disabled={busy}/> Use AI to answer my questions (this question is sent to an external AI provider; avoid personal information).</label>}<button className="primary" disabled={busy||!draft.trim()}>{busy?"Sending…":"Send message"}</button></form><p className="muted">Messages refresh automatically. Do not share passwords, payment card details or identity documents here.</p></>:<p>Select a conversation to read and reply.</p>}</section>
}
