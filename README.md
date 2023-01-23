# Pik-Fix: Restoring and Colorizing Old Photo
[![paper](https://img.shields.io/badge/arXiv-Paper-<COLOR>.svg)](https://arxiv.org/pdf/2205.01902.pdf)
[![supplement](https://img.shields.io/badge/Supplementary-Material-red)]()
[![video](https://img.shields.io/badge/Video-Presentation-F9D371)]()

The official code implementation of WACV2023 paper "Pik-Fix: Restoring and Colorizing Old Photos".

## Real Old Photo Data
### Download
Due to copyright restrictions, our real old photo dataset is only available upon personal inquiry. To request access, please email derrickxu1994@gmail.com with the subject "Pik-Fix Data Inquiry" and include your name and affiliation in the message.
Then we will reply to you with the download link in 3 days.

### File Structure
There will be two folders in  the shared data link: `real_old_data` and `real_old_ref`. The first one contains
200 image pair with old photos and the repared ones. The second folder contains the reference images we used in our paper.


## Installation
```python
conda create -n pikfix python=3.7
conda activate pikfix
conda install pytorch==1.2.0 torchvision==0.4.0 cudatoolkit=10.0 -c pytorch
pip install -r requirements.txt
```