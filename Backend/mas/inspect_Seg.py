import nibabel as nib
import numpy as np

# Paths
image_path = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\volumes_data\1.2.826.0.1.3680043.8.498.12073285427930663774611907954116957406.nii.gz"      # mask / image volume
seg_path   = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\labels_data\1.2.826.0.1.3680043.8.498.12073285427930663774611907954116957406.nii.gz"

# Load volumes
img_nii = nib.load(image_path)
seg_nii = nib.load(seg_path)

img = img_nii.get_fdata()
seg = seg_nii.get_fdata()

# Inspect segmentation
unique_vals = np.unique(seg)
print("Unique values in segmentation:", unique_vals)

# Simple interpretation
if set(unique_vals).issubset({0, 1}):
    print("Segmentation is binary: {0, 1}")
elif set(unique_vals).issubset({0, 255}):
    print("Segmentation is binary: {0, 255}")
else:
    print("Segmentation is multi-class or unexpected")
    
if np.max(seg) == 255:
    seg = (seg > 0).astype(np.uint8)
    print("Converted segmentation to {0,1}")
    
import matplotlib.pyplot as plt
import imageio
import os

output_gif = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\overlay.gif"
frames = []

# Normalize image for visualization
img_norm = (img - img.min()) / (img.max() - img.min())

for z in range(img.shape[2]):
    if np.sum(seg[:, :, z]) == 0:
        continue  # skip empty slices (optional)

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.imshow(img_norm[:, :, z], cmap="gray")

    # Overlay segmentation (red)
    ax.imshow(seg[:, :, z], cmap="Reds", alpha=0.4)

    ax.set_title(f"Slice {z}")
    ax.axis("off")

    # Save frame
    fig.canvas.draw()
    frame = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
    frame = frame.reshape(fig.canvas.get_width_height()[::-1] + (3,))
    frames.append(frame)

    plt.close(fig)

# Save GIF
imageio.mimsave(output_gif, frames, fps=8)

print(f"GIF saved as: {output_gif}")


