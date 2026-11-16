import fs from 'fs';

let content = fs.readFileSync('ThreadRouteView-D5J-hl3X.orig.js', 'utf8');

// 1. In outer component, update yt useMemo
const targetYt = '_t=$s(ct),vt=Gs(ct),yt=(0,Z.useMemo)(()=>{if(ct!==null&&hi(vt))return{hasMoreHistory:vt.hasMoreHistory,loading:vt.loading,error:vt.error,onLoadEarlier:e=>{we({environmentId:ct.environmentId,input:{threadId:ct.threadId,...e===void 0?{}:{throughEntryId:e}}})}}},[we,ct,vt])';

const replYt = '_t=$s(ct),vt=Gs(ct),yt=(0,Z.useMemo)(()=>{if(ct!==null&&vt)return{hasMoreHistory:!!vt.hasMoreHistory,loading:!!vt.loading,error:vt.error,addedItems:vt.addedItems,emptyPageStreak:vt.emptyPageStreak??0,pageCount:vt.pageCount??0,lastPageReason:vt.lastPageReason,onLoadEarlier:e=>{we({environmentId:ct.environmentId,input:{threadId:ct.threadId,...e===void 0?{}:{throughEntryId:e}}})}}},[we,ct,vt])';

if (!content.includes(targetYt)) throw new Error('targetYt not found');
content = content.replace(targetYt, replYt);

// 2. Replace BU function with Section A7 compliant version
const targetBU = `function BU(e){let t=(0,Q.c)(7);if(!e.hasMoreHistory&&e.error===null)return null;let n;t[0]===e?n=t[1]:(n=e.hasMoreHistory?(0,$.jsx)(\`button\`,{type:\`button\`,disabled:e.loading,"aria-label":\`Load earlier turns\`,onClick:()=>e.onLoadEarlier(),className:\`w-full py-1.5 text-xs text-muted-foreground/60 hover:text-foreground disabled:cursor-default\`,children:e.loading?\`Loading earlier turns…\`:\`Load earlier turns\`}):null,t[0]=e,t[1]=n);let r;t[2]===e.error?r=t[3]:(r=e.error===null?null:(0,$.jsx)(\`p\`,{role:\`status\`,className:\`text-center text-muted-foreground text-xs\`,children:e.error}),t[2]=e.error,t[3]=r);let i;return t[4]!==n||t[5]!==r?(i=(0,$.jsx)(\`div\`,{className:\`messages-timeline-row-frame\`,children:(0,$.jsxs)(\`div\`,{className:\`chat-content-lane flex flex-col gap-1.5 pb-2\`,children:[n,r]})}),t[4]=n,t[5]=r,t[6]=i):i=t[6],i}`;

const replBU = `function BU(e){if(!e)return null;let n;if(e.loading){n=(0,$.jsxs)(\`div\`,{className:\`flex h-8 items-center justify-center gap-2 text-muted-foreground\`,children:[(0,$.jsx)(Da,{size:14,className:\`animate-spin\`,"aria-label":\`Loading earlier turns…\`}),(0,$.jsx)(\`span\`,{className:\`text-xs\`,children:\`Loading earlier turns…\`})]}) }else if(e.error){n=(0,$.jsxs)(\`div\`,{role:\`status\`,className:\`flex items-center justify-center gap-2 py-1.5\`,children:[(0,$.jsx)(\`span\`,{className:\`text-xs text-muted-foreground\`,children:e.error}),(0,$.jsx)(\`button\`,{type:\`button\`,onClick:()=>e.onLoadEarlier?.({isManual:!0}),className:\`text-xs text-primary hover:underline\`,children:\`Retry\`})]}) }else if(e.hasMoreHistory){n=(0,$.jsx)(\`button\`,{type:\`button\`,"aria-label":\`Load earlier turns\`,onClick:()=>e.onLoadEarlier?.({isManual:!0}),className:\`w-full rounded-md border border-border/60 bg-card px-3 py-1.5 text-xs font-medium text-foreground/80 hover:bg-accent\`,children:\`Load earlier turns\`}) }else{n=(0,$.jsx)(\`div\`,{className:\`py-1.5 text-center text-xs text-muted-foreground/70\`,children:\`Beginning of thread\`}) }return(0,$.jsx)(\`div\`,{role:\`region\`,"aria-label":\`Thread history\`,className:\`messages-timeline-row-frame\`,children:(0,$.jsx)(\`div\`,{className:\`chat-content-lane flex flex-col gap-1.5 pb-2\`,children:n})})}`;

if (!content.includes(targetBU)) throw new Error('targetBU not found');
content = content.replace(targetBU, replBU);

// 3. Insert autoLoad, anchors, toasts, scroll wiring in zU right before Jt definition
const targetJtBlock = `,Jt=(0,Z.useMemo)(()=>{let e=j===null?ve?jU:AU:(0,$.jsx)(\`div\`,{className:\`messages-timeline-row-frame\`,children:(0,$.jsx)(\`div\`,{className:\`chat-content-lane pt-1 sm:pt-2\`,children:(0,$.jsx)(YH,{label:\`Subagent of\`,detail:j.title,icon:Nv,actionLabel:\`Open parent thread\`,onAction:()=>A(j.threadId)})})});return(0,$.jsxs)($.Fragment,{children:[j===null?e:null,ye?(0,$.jsx)(BU,{...ye}):null,j===null?null:e]})},[ye,A,j,ve])`;

const replJtBlock = `;
const __HIST_TOAST_ID="history-load";
const __prevPageCount=(0,Z.useRef)(ye?.pageCount??0);
const __anchorRef=(0,Z.useRef)(null);
const __pendingCorrection=(0,Z.useRef)(!1);
const __lateCorrectionUntil=(0,Z.useRef)(0);
const __isApplyingCorrection=(0,Z.useRef)(!1);
const __lastUserScrollTime=(0,Z.useRef)(0);
const __LATE_WINDOW_MS=500;
const [__initialSettled,__setInitialSettled]=(0,Z.useState)(!1);
const __pagesWithoutInput=(0,Z.useRef)(0);
const __lastLoadAt=(0,Z.useRef)(0);
const __prevScrollTop=(0,Z.useRef)(0);
const __PREFETCH_RATIO=0.8;
const __MAX_WITHOUT_INPUT=3;
const __MIN_INTERVAL_MS=250;
const __SOFT_LIMIT_PAGES=30;
const __LEAVE_TOP_MULT=1.5;

const __getScrollMetrics=(0,Z.useCallback)(()=>{
  let st=_.current?.getState?.();
  if(st&&Number.isFinite(st.scroll)&&Number.isFinite(st.scrollLength)){
    return{scrollTop:Math.max(0,st.scroll),scrollLength:st.scrollLength};
  }
  let el=_.current?.getScrollableNode();
  if(!el)return null;
  return{scrollTop:el.scrollTop,scrollLength:el.clientHeight};
},[_]);

const __captureAnchor=(0,Z.useCallback)(()=>{
  let el=_.current?.getScrollableNode();
  if(!el)return null;
  let rows=el.querySelectorAll("[data-timeline-row-id]");
  let top=el.getBoundingClientRect().top;
  for(let i=0;i<rows.length;i++){
    let r=rows[i],rect=r.getBoundingClientRect();
    if(rect.bottom>top+1){
      let id=r.getAttribute("data-timeline-row-id");
      if(id)return{rowId:id,top:rect.top};
    }
  }
  return null;
},[_]);

const __applyCorrection=(0,Z.useCallback)(()=>{
  let anchor=__anchorRef.current;
  if(!anchor)return;
  let scroller=_.current?.getScrollableNode();
  if(!scroller)return;
  let el=scroller.querySelector(\`[data-timeline-row-id="\${CSS.escape(anchor.rowId)}"]\`);
  if(!el)return;
  let drift=el.getBoundingClientRect().top-anchor.top;
  if(Math.abs(drift)>2){
    __isApplyingCorrection.current=!0;
    __prevScrollTop.current+=drift;
    scroller.scrollTop+=drift;
    queueMicrotask(()=>{__isApplyingCorrection.current=!1});
  }
},[_]);

const __cancelLateCorrection=(0,Z.useCallback)(()=>{
  __lateCorrectionUntil.current=0;
  __anchorRef.current=null;
},[]);

const __requestLoadEarlier=(0,Z.useCallback)((opts={})=>{
  if(!ye||ye.loading)return;
  __setInitialSettled(!0);
  let throughEntryId=opts.throughEntryId;
  if(throughEntryId===void 0){
    __anchorRef.current=__captureAnchor();
    __pendingCorrection.current=!0;
    if(opts.isManual){
      Fs.add({id:__HIST_TOAST_ID,type:"loading",title:"Loading earlier turns…",timeout:0});
    }
  }
  ye.onLoadEarlier?.(throughEntryId===void 0?void 0:throughEntryId);
},[ye,__captureAnchor]);

const __isWithinPreload=(0,Z.useCallback)(()=>{
  let m=__getScrollMetrics();
  if(!m)return!1;
  return m.scrollTop<=__PREFETCH_RATIO*m.scrollLength;
},[__getScrollMetrics]);

const __tryLoad=(0,Z.useCallback)((source)=>{
  let h=ye;
  if(!h||!h.hasMoreHistory)return;
  if(!__initialSettled)return;
  if(Ze||x!==null)return;
  if(h.loading||h.error!==null)return;
  if((h.emptyPageStreak??0)>=2)return;
  if((h.pageCount??0)>=__SOFT_LIMIT_PAGES)return;
  if(source==="startReached"&&__pagesWithoutInput.current>=__MAX_WITHOUT_INPUT)return;
  if(Date.now()-__lastLoadAt.current<__MIN_INTERVAL_MS)return;
  if(!__isWithinPreload())return;

  __pagesWithoutInput.current+=1;
  __lastLoadAt.current=Date.now();
  __requestLoadEarlier();
},[ye,__initialSettled,Ze,x,__isWithinPreload,__requestLoadEarlier]);

const __handleStartReached=(0,Z.useCallback)(()=>__tryLoad("startReached"),[__tryLoad]);

const __handleUserScrollIntent=(0,Z.useCallback)(()=>{
  __pagesWithoutInput.current=0;
  __tryLoad("userInput");
},[__tryLoad]);

const __handleAutoLoadScroll=(0,Z.useCallback)((scrollTop)=>{
  let delta=scrollTop-__prevScrollTop.current;
  __prevScrollTop.current=scrollTop;
  if(delta>0.5){
    __pagesWithoutInput.current=0;
    return;
  }
  if(delta<-0.5)return;
  let m=__getScrollMetrics();
  if(m&&scrollTop>__PREFETCH_RATIO*__LEAVE_TOP_MULT*m.scrollLength){
    __pagesWithoutInput.current=0;
  }
},[__getScrollMetrics]);

const __resetForThread=(0,Z.useCallback)(()=>{
  __pagesWithoutInput.current=0;
  __lastLoadAt.current=0;
  __prevScrollTop.current=0;
},[]);

const __handleScroll=(0,Z.useCallback)((s)=>{
  Ut(s);
  let st=typeof s?.scrollTop==="number"?s.scrollTop:(typeof s?.nativeEvent?.contentOffset?.y==="number"?s.nativeEvent.contentOffset.y:(_.current?.getScrollableNode()?.scrollTop??0));
  let isUser=(s?.isUserDriven??(Date.now()-__lastUserScrollTime.current<1000))&&!__isApplyingCorrection.current&&!s?.scrollToIndexInProgress;
  if(!__initialSettled&&isUser){
    __setInitialSettled(!0);
    __handleUserScrollIntent();
  }
  if(isUser){
    __cancelLateCorrection();
  }
  __handleAutoLoadScroll(st);
},[Ut,_,__initialSettled,__handleUserScrollIntent,__cancelLateCorrection,__handleAutoLoadScroll]);

const __handleItemSizeChanged=(0,Z.useCallback)((rowId)=>{
  if(__lateCorrectionUntil.current>Date.now())__applyCorrection();
  Ht(rowId);
},[Ht,__applyCorrection]);

(0,Z.useEffect)(()=>{
  let el=_.current?.getScrollableNode();
  let registerTarget=el||window;
  let onUserGesture=()=>{
    __lastUserScrollTime.current=Date.now();
  };
  let onWheel=(e)=>{
    onUserGesture();
    if(e.deltaY<0){
      if(!__initialSettled)__setInitialSettled(!0);
      __handleUserScrollIntent();
    }
  };
  let onTouch=()=>{
    onUserGesture();
    if(!__initialSettled)__setInitialSettled(!0);
    __handleUserScrollIntent();
  };
  let onKey=(e)=>{
    if(e.ctrlKey||e.metaKey||e.altKey)return;
    if(e.key==="PageUp"||e.key==="ArrowUp"||e.key==="Home"){
      onUserGesture();
      if(!__initialSettled)__setInitialSettled(!0);
      __handleUserScrollIntent();
    }
  };
  registerTarget.addEventListener("wheel",onWheel,{passive:!0});
  registerTarget.addEventListener("touchstart",onTouch,{passive:!0});
  window.addEventListener("keydown",onKey);
  return()=>{
    registerTarget.removeEventListener("wheel",onWheel);
    registerTarget.removeEventListener("touchstart",onTouch);
    window.removeEventListener("keydown",onKey);
  };
},[_,__initialSettled,__handleUserScrollIntent]);

(0,Z.useLayoutEffect)(()=>{
  if(!__pendingCorrection.current)return;
  __pendingCorrection.current=!1;
  __applyCorrection();
  __lateCorrectionUntil.current=Date.now()+__LATE_WINDOW_MS;
},[ye?.pageCount,__applyCorrection]);

(0,Z.useEffect)(()=>{
  if(__lateCorrectionUntil.current===0)return;
  let t=window.setTimeout(__cancelLateCorrection,__LATE_WINDOW_MS);
  return()=>window.clearTimeout(t);
},[ye?.pageCount,__cancelLateCorrection]);

(0,Z.useEffect)(()=>{
  if(ye?.loading)return;
  if(__pendingCorrection.current){
    __pendingCorrection.current=!1;
    __anchorRef.current=null;
  }
},[ye?.loading,ye?.pageCount]);

(0,Z.useEffect)(()=>{
  let h=ye;
  if(!h)return;
  if(h.pageCount===__prevPageCount.current)return;
  __prevPageCount.current=h.pageCount;
  if(h.lastPageReason==="jump")return;

  let added=h.addedItems??0;
  Fs.add({
    id:__HIST_TOAST_ID,
    type:added>0?"success":"info",
    title:added>0?"Loaded earlier messages":"No earlier messages were added",
    ...(added>0?{description:\`\${added} messages added\`}:{}),
    timeout:2000
  });
},[ye?.pageCount,ye?.addedItems,ye?.lastPageReason]);

(0,Z.useEffect)(()=>{
  if(!ye?.error)return;
  Fs.add({
    id:__HIST_TOAST_ID,
    type:"warning",
    title:"Could not load earlier activity",
    description:ye.error,
    timeout:4000,
    actionProps:{children:"Retry",onClick:()=>__requestLoadEarlier({isManual:!0})}
  });
},[ye?.error,__requestLoadEarlier]);

(0,Z.useEffect)(()=>{
  __setInitialSettled(!1);
  __resetForThread();
  __anchorRef.current=null;
  __pendingCorrection.current=!1;
  __lateCorrectionUntil.current=0;
  __prevPageCount.current=ye?.pageCount??0;
  Fs.close(__HIST_TOAST_ID);
  return()=>{Fs.close(__HIST_TOAST_ID)};
},[xe,__resetForThread]);

let Jt=(0,Z.useMemo)(()=>{let e=j===null?ve?jU:AU:(0,$.jsx)(\`div\`,{className:\`messages-timeline-row-frame\`,children:(0,$.jsx)(\`div\`,{className:\`chat-content-lane pt-1 sm:pt-2\`,children:(0,$.jsx)(YH,{label:\`Subagent of\`,detail:j.title,icon:Nv,actionLabel:\`Open parent thread\`,onAction:()=>A(j.threadId)})})});return(0,$.jsxs)($.Fragment,{children:[j===null?e:null,ye?(0,$.jsx)(BU,{...ye,onLoadEarlier:__requestLoadEarlier}):null,j===null?null:e]})},[ye,A,j,ve,__requestLoadEarlier])`;

if (!content.includes(targetJtBlock)) throw new Error('targetJtBlock not found');
content = content.replace(targetJtBlock, replJtBlock);

// 4. Update LegendList props in Sv invocation
const targetSvProps = 'maintainScrollAtEndThreshold:1,onScroll:Ut,onItemSizeChanged:Ht,className:';
const replSvProps = 'maintainScrollAtEndThreshold:1,onStartReached:__handleStartReached,onStartReachedThreshold:__PREFETCH_RATIO,onScroll:__handleScroll,onItemSizeChanged:__handleItemSizeChanged,className:';

if (!content.includes(targetSvProps)) throw new Error('targetSvProps not found');
content = content.replace(targetSvProps, replSvProps);

fs.writeFileSync('ThreadRouteView-D5J-hl3X.patched.js', content);
console.log('ThreadRouteView-D5J-hl3X.patched.js written successfully');
