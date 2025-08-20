for i in {0..2}; do   # 0-1, 1-2, 2-3 …
  ffmpeg -i $i.mp4 -i $((i+1)).mp4 \
         -filter_complex "[0][1]xfade=transition=fade:duration=1:offset=0" \
         -t 1 -c:v libx264 -an xfade_${i}.mp4
done
