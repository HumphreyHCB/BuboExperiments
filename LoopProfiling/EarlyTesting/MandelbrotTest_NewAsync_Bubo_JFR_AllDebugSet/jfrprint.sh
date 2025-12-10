JFR=Mandelbrot_JFRRuns/Mandelbrot200/Mandelbrot_200_JFR_Slowdown_BuboOff.jfr

jfr print --events ExecutionSample "$JFR" | awk '
/^jdk\.ExecutionSample \{/ {in_event=1; has_bubo=0; next}
in_event {
  if ($0 ~ /BuboAgentCompilerMarkers/) has_bubo=1;
  if ($0 ~ /^}/) {
    in_event=0;
  }
}
/stackTrace = \[/ && has_bubo {
  print "===== STACKTRACE WITH BUBO =====";
  print $0;
  in_stack=1;
  next;
}
in_stack {
  print $0;
  if ($0 ~ /^\]/) {
    in_stack=0;
    print "";
  }
}
'
